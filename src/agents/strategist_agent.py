"""
src/agents/strategist_agent.py
Multi-model LLM Strategist Gateway combining Google Gemini and Featherless Open-Source Models.

Features:
- Seamless dual-provider switching via `settings.LLM_PROVIDER` ('gemini' vs 'featherless')
- Featherless AI: AsyncOpenAI client targeting serverless open-source models (Qwen, GLM) with
  automatic 3-attempt exponential backoff retry on HTTP 503 capacity errors.
- Google Gemini: google.genai client targeting Gemini 2.0 Flash with structured JSON output.
- Deterministic fallback for offline testing and mock simulations.
- Outputs strongly-typed TradeProposal models verified by deterministic Hard Risk Gates.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

from openai import AsyncOpenAI

from src.agents.base_agent import BaseAgent
from src.core.config import settings
from src.core.event_bus import EventBus, MarketTickEvent, SignalEvent, event_bus as default_event_bus
from src.core.exceptions import LLMProviderError
from src.data.chain_parser import ParsedOptionChain
from src.data.regime_detector import MarketRegime, RegimeClassification
from src.execution.order_builder import format_occ_symbol
from src.risk.hard_gates import TradeProposal

logger = logging.getLogger("agent.strategist")


class StrategistAgent(BaseAgent):
    """
    Cognitive strategist formulating defined-risk options trade proposals
    using Google Gemini or Featherless open-source LLMs.
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        provider: Optional[str] = None,
        mock_mode: bool = False,
    ) -> None:
        super().__init__(name="strategist", event_bus=event_bus)
        self.provider: str = provider or settings.LLM_PROVIDER
        self.mock_mode: bool = mock_mode

        self._featherless_client: Optional[AsyncOpenAI] = None
        self._gemini_client: Optional[Any] = None

        self._init_clients()

    def _init_clients(self) -> None:
        """Initializes API clients based on active provider configuration."""
        if self.mock_mode:
            self.logger.info("StrategistAgent initialized in mock mode.")
            return

        # 1. Featherless AI Client (OpenAI SDK Compatible)
        if settings.FEATHERLESS_API_KEY:
            try:
                self._featherless_client = AsyncOpenAI(
                    base_url=settings.FEATHERLESS_BASE_URL,
                    api_key=settings.FEATHERLESS_API_KEY,
                )
                self.logger.info(
                    "Featherless AsyncOpenAI client initialized (Model: %s).",
                    settings.FEATHERLESS_MODEL,
                )
            except Exception as exc:
                self.logger.warning("Failed to initialize Featherless client: %s", exc)

        # 2. Google Gemini Client
        if settings.GEMINI_API_KEY:
            try:
                from google.genai import Client as GenAIClient
                self._gemini_client = GenAIClient(api_key=settings.GEMINI_API_KEY)
                self.logger.info("Google Gemini client initialized (Model: %s).", settings.GEMINI_MODEL)
            except Exception as exc:
                self.logger.warning("Failed to initialize Gemini client: %s", exc)

    async def start(self) -> None:
        """Starts strategist lifecycle."""
        self._running = True
        self.telemetry.is_running = True
        self.logger.info("StrategistAgent started. Provider: %s", self.provider)

    async def stop(self) -> None:
        """Stops strategist lifecycle."""
        self._running = False
        self.telemetry.is_running = False
        self.logger.info("StrategistAgent stopped.")

    async def formulate_strategy(
        self,
        underlying: str,
        current_price: float,
        regime: RegimeClassification,
        chain: Optional[ParsedOptionChain] = None,
    ) -> Optional[TradeProposal]:
        """
        Formulates an options spread proposal tailored to the detected market regime.
        Routes to Gemini or Featherless based on settings.LLM_PROVIDER.
        """
        # If market is in low-volatility chop, enforce cash preservation immediately
        if regime.regime == MarketRegime.LOW_IV_CHOP:
            self.logger.info("Market regime is LOW_IV_CHOP for %s. Preserving cash.", underlying)
            return None

        # Build prompt payload
        prompt = self._build_prompt(underlying, current_price, regime)

        # Mock / Test Fallback
        if self.mock_mode or (self.provider == "gemini" and not self._gemini_client) or (
            self.provider == "featherless" and not self._featherless_client
        ):
            self.logger.info("Executing synthetic deterministic strategy formulation for %s.", underlying)
            proposal = self._generate_synthetic_proposal(underlying, current_price, regime)
            self.telemetry.proposals_generated += 1
            self.record_activity()
            return proposal

        # Live LLM Inference
        try:
            if self.provider == "featherless":
                raw_json = await self._call_featherless_with_retry(prompt)
            elif self.provider == "gemini":
                raw_json = await self._call_gemini(prompt)
            else:
                raise LLMProviderError(self.provider, f"Unknown LLM provider: {self.provider}")

            proposal = self._parse_llm_json_to_proposal(raw_json, underlying, regime)
            self.telemetry.proposals_generated += 1
            self.record_activity()
            return proposal

        except Exception as exc:
            self.logger.error("LLM strategy formulation error: %s", exc)
            self.telemetry.errors_encountered += 1
            # Fallback to deterministic proposal to protect trading continuity
            return self._generate_synthetic_proposal(underlying, current_price, regime)

    async def _call_featherless_with_retry(self, prompt: str, max_retries: int = 3) -> str:
        """
        Calls Featherless OpenAI-compatible API with automatic 3-attempt exponential
        backoff retry on HTTP 503 capacity errors.
        """
        if not self._featherless_client:
            raise LLMProviderError("featherless", "Featherless client is not configured")

        backoff_delays = [1.0, 2.0, 4.0]
        last_exception = None

        for attempt in range(max_retries):
            try:
                self.logger.info(
                    "Sending prompt to Featherless (Attempt %d/%d, Model: %s)",
                    attempt + 1,
                    max_retries,
                    settings.FEATHERLESS_MODEL,
                )
                response = await self._featherless_client.chat.completions.create(
                    model=settings.FEATHERLESS_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an elite quantitative options strategist. Output strict JSON only. "
                                "Never include markdown preamble or explanations outside JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=800,
                )
                if not response or not getattr(response, "choices", None) or len(response.choices) == 0:
                    raise ValueError("Featherless returned an empty response or empty choices list.")
                first_choice = response.choices[0]
                if not first_choice or not getattr(first_choice, "message", None):
                    raise ValueError("Featherless response choice missing message structure.")
                content = getattr(first_choice.message, "content", "") or ""
                return content.strip()

            except Exception as exc:
                err_str = str(exc)
                last_exception = exc
                is_503_or_rate = "503" in err_str or "capacity" in err_str.lower() or "429" in err_str
                if is_503_or_rate and attempt < max_retries - 1:
                    delay = backoff_delays[attempt]
                    self.logger.warning(
                        "Featherless capacity/rate limit (503/429). Retrying in %.1fs... (%s)",
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    self.logger.error("Featherless API attempt %d failed: %s", attempt + 1, exc)
                    if attempt == max_retries - 1:
                        break

        raise LLMProviderError("featherless", f"Failed after {max_retries} attempts: {last_exception}")

    async def _call_gemini(self, prompt: str) -> str:
        """Invokes Google Gemini API with JSON mode."""
        if not self._gemini_client:
            raise LLMProviderError("gemini", "Gemini client is not configured")

        try:
            self.logger.info("Sending prompt to Gemini (Model: %s)", settings.GEMINI_MODEL)
            # Run synchronously in thread pool
            response = await asyncio.to_thread(
                self._gemini_client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=prompt,
            )
            return response.text or ""
        except Exception as exc:
            self.logger.error("Google Gemini API error: %s", exc)
            raise LLMProviderError("gemini", str(exc))

    def _build_prompt(
        self,
        underlying: str,
        current_price: float,
        regime: RegimeClassification,
    ) -> str:
        """Constructs an institutional quantitative prompt for the LLM."""
        return f"""
Analyze the following market condition and propose an institutional options spread trade.

Asset: {underlying}
Current Price: ${current_price:.2f}
Regime: {regime.regime.value}
Recommended Strategy: {regime.recommended_strategy}
Trend: {regime.trend_direction.value}
Implied Volatility Rank (IVR): {regime.ivr:.1f}%
Implied Volatility Percentile (IVP): {regime.ivp:.1f}%
ADX (14-period): {regime.adx:.1f}
Volatility Premium (IV - HV): {regime.vol_premium * 100:.2f}%

Respond with strict JSON matching this schema:
{{
  "strategy_name": "{regime.recommended_strategy}",
  "thesis": "Quantitative justification for spread strikes and expiration selection",
  "dte": 30,
  "short_strike": 540.0,
  "long_strike": 530.0,
  "target_credit": 1.20,
  "max_loss": 380.0,
  "quantity": 1
}}
"""

    def _parse_llm_json_to_proposal(
        self,
        raw_text: str,
        underlying: str,
        regime: RegimeClassification,
    ) -> TradeProposal:
        """Parses LLM JSON output into a strongly-typed TradeProposal."""
        clean_text = raw_text.strip()
        # Strip markdown fences if present
        if clean_text.startswith("```"):
            clean_text = re.sub(r"^```(?:json)?\n", "", clean_text)
            clean_text = re.sub(r"\n```$", "", clean_text)

        data = json.loads(clean_text)

        strategy = data.get("strategy_name", regime.recommended_strategy)
        dte = int(data.get("dte", 30))
        target_credit = float(data.get("target_credit", 1.0)) * 100.0  # Dollars per contract
        max_loss = float(data.get("max_loss", 400.0))
        qty = int(data.get("quantity", 1))
        thesis = data.get("thesis", "Algorithmic regime edge")

        # Estimate required margin from max loss + credit
        required_margin = max_loss + target_credit

        return TradeProposal(
            symbol=underlying.upper(),
            strategy_name=strategy,
            quantity=qty,
            max_loss_per_contract=max_loss,
            target_credit_per_contract=target_credit,
            required_margin_per_contract=required_margin,
            dte=dte,
            is_tactical=False,
            thesis=thesis,
            max_profit=target_credit * qty,
            max_loss=max_loss * qty,
            ivr=regime.ivr,
            regime=regime.regime.value,
        )

    def _generate_synthetic_proposal(
        self,
        underlying: str,
        current_price: float,
        regime: RegimeClassification,
    ) -> TradeProposal:
        """
        Deterministic proposal generator matching the detected regime for offline testing
        and live fallback.
        """
        dte = 30
        if regime.regime == MarketRegime.HIGH_IV_RANGEBOUND:
            # Iron Condor: 5-point wide wings
            strategy = "Iron Condor"
            credit = 150.0  # $1.50 credit
            margin = 500.0  # $5.00 wing width
            max_loss = margin - credit
            thesis = f"High IVR ({regime.ivr:.1f}%) with rangebound chop (ADX={regime.adx:.1f}). Neutral decay harvesting."

        elif regime.regime == MarketRegime.HIGH_IV_TRENDING:
            if regime.trend_direction == "BEARISH":
                strategy = "Bear Call Credit Spread"
                credit = 120.0
                margin = 500.0
                max_loss = margin - credit
                thesis = f"High IV ({regime.ivr:.1f}%) downtrend (ADX={regime.adx:.1f}). Selling OTM call premium."
            else:
                strategy = "Bull Put Credit Spread"
                credit = 120.0
                margin = 500.0
                max_loss = margin - credit
                thesis = f"High IV ({regime.ivr:.1f}%) uptrend (ADX={regime.adx:.1f}). Selling OTM put premium."

        else:
            # LOW_IV_TRENDING
            strategy = "Bull Call Debit Spread"
            credit = 0.0
            max_loss = 250.0  # $2.50 debit
            margin = 250.0
            thesis = f"Low IV ({regime.ivr:.1f}%) trend continuation. Capping theta drag with debit vertical."

        return TradeProposal(
            symbol=underlying.upper(),
            strategy_name=strategy,
            quantity=1,
            max_loss_per_contract=max_loss,
            target_credit_per_contract=credit,
            required_margin_per_contract=margin,
            dte=dte,
            is_tactical=False,
            thesis=thesis,
            max_profit=credit,
            max_loss=max_loss,
            ivr=regime.ivr,
            regime=regime.regime.value,
        )
