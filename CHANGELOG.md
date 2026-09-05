# Changelog
All notable changes to the **OptionForge** autonomous options trading system will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.3.0] - 2026-09-05

### Added
- **Market-Hours Trading Gate & Standby Mode (`src/execution/alpaca_client.py`, `src/cli/main.py`)**:
  - Implemented automatic US Regular Trading Hours (RTH 09:30 – 16:00 ET) detection via `AlpacaExecutionClient.get_market_clock()` querying `TradingClient.get_clock()`.
  - Added strongly-typed `MarketClockState` Pydantic model with automatic human-readable countdown calculation (`countdown_to_open_str`, e.g., `"2d 19h 4m"`).
  - During market-closed hours (nights, weekends, holidays), OptionForge automatically enters **Standby Mode**:
    - Pauses all strategy formulation requests to Featherless AI and Google Gemini (0 API calls made, zero quota wasted).
    - Pauses new order submissions and contract chain downloads.
    - Pauses active market scanning while safely retaining the last-known candidate snapshot.
    - Dynamically throttles main loop refresh sleep from 2.0s down to **30.0 seconds**, keeping CPU consumption near zero and Alpaca clock polls under 2 requests/minute.
  - Added `--force-eval` (alias `--ignore-market-hours`) CLI flag to `run-paper` to allow intentional off-hours evaluation, prompt testing, and debugging.
- **Standby Telemetry & Terminal HUD Banners**:
  - Renders a prominent `[STANDBY MODE ACTIVE]` banner on the Rich console dashboard displaying remaining countdown and exact session open timestamp.
  - Displays `[FORCE-EVAL ACTIVE]` startup notice and dashboard banner when the developer override is active.
- **Unit & Integration Tests (`tests/unit/test_execution.py`, `tests/unit/test_cli.py`)**:
  - Added `test_market_clock_countdown_formatting` validating days, hours, and minutes string formatting.
  - Added `test_get_market_clock_mock_mode`, `test_get_market_clock_with_trading_client`, and `test_get_market_clock_fallback_on_exception`.
  - Added `test_cli_run_paper_market_closed_standby` verifying that `formulate_strategy` is NEVER called when closed.
  - Added `test_cli_run_paper_force_eval_overrides_market_closed` verifying execution when `--force-eval` is passed.

### Fixed
- **Windows Console Encoding Compatibility (`src/cli/main.py`)**:
  - Replaced non-ASCII Unicode emojis/symbols (`⚡`, `⏸`, `●`, `•`, `—`) with ASCII-clean badges (`[FORCE-EVAL ACTIVE]`, `[STANDBY]`, `[OPEN]`, `[?]`, `-`) to eliminate Windows `UnicodeEncodeError` under `cp1252` codepage consoles.

---

## [1.2.0] - 2026-09-05

### Added
- **Dynamic Real-World Listed Strike Discovery (`src/execution/alpaca_client.py`, `src/cli/main.py`)**:
  - Implemented `find_real_option_spread_legs()` on `AlpacaExecutionClient`, querying Alpaca's live contract catalog (`client.get_option_contracts`) to select authentic, listed exchange strikes within the 14–45 DTE window.
  - Replaced theoretical mathematical strike calculation with real listed strike snapping, preventing Alpaca HTTP 422 `invalid legs: asset not found` order rejections.
  - Added target credit bounding ensuring credits are realistically priced between 25% and 30% of actual strike width.
- **Candidate Waterfalling (`src/cli/main.py`)**:
  - Implemented robust candidate waterfall loop: if contract discovery, pricing, or risk gating fails on the top candidate, the agent automatically waterfalls to the next highest edge-score asset in the universe.

### Fixed
- **CLI Module Circular Import Warning (`src/cli/__init__.py`)**:
  - Removed internal submodule re-exports from `src/cli/__init__.py` to eliminate Python `runpy` RuntimeWarning when executing `python -m src.cli`.
- **LLM Strategist Robust JSON Parsing (`src/agents/strategist_agent.py`)**:
  - Enhanced markdown regex extraction to handle triple-backtick fenced blocks, unbracketed JSON, and empty responses with automatic deterministic fallback.

---

## [1.1.0] - 2026-09-04

### Added
- **Interactive Live LLM Strategist Playground (`streamlit_app.py` Tab 4)**:
  - Added interactive browser playground allowing judges and users to test Featherless AI (`Qwen/Qwen2.5-72B-Instruct`, `zai-org/GLM-5.2`) and Google Gemini (`gemini-2.0-flash`) in real-time with customizable tickers, prices, and regimes.
- **Real-Time Execution Countdown**:
  - Added market open session countdown in the sidebar and Tab 1.
- **Public Cloud Hosting Guide (`docs/hackathon/hosting_guide.md`)**:
  - Published comprehensive deployment guides for Streamlit Community Cloud, Replit, Render, Railway, and Vercel.
- **Autonomous Trading Journey Guide (`docs/hackathon/autonomous_trading_journey.md`)**:
  - Published exhaustive 7-stage architectural documentation explaining the decoupling of cognitive LLM reasoning from deterministic risk protection.

### Fixed
- **Light Theme Contrast & Header Visibility (`streamlit_app.py`)**:
  - Replaced hardcoded CSS header styles with dynamic theme-aware typography using Google Fonts (Outfit, JetBrains Mono) ensuring crisp visibility across both Light and Dark modes.
- **Deprecated Streamlit Parameters (`streamlit_app.py`)**:
  - Replaced deprecated `use_container_width=True` on metric cards and buttons with modern native layout patterns.

---

## [1.0.0] - 2026-09-03

### Added
- **Turnkey CLI Entrypoint (`src/cli/main.py`)**:
  - Rich-powered interactive terminal dashboard displaying real-time KPI metrics, 52-week IV ranking tables, open position trackers, and risk gate health statuses.
  - Added CLI commands: `run-paper`, `test-risk-gate`, `inspect-account`, and `attribution-report`.
  - Added runtime flags: `--account` (`test` vs `competition`), `--llm-provider` (`featherless` vs `gemini`), `--interval`, `--bypass`, and `--mock`.
- **Position Monitor & Profit Harvester (`src/agents/monitor_agent.py`)**:
  - Automated 60% profit target harvesting via inverted limit-to-close orders.
  - Automated 2.5x stop-loss capital protection.
  - Automated 3 DTE expiration pin-risk defense closing positions before gamma expansion.
- **Deterministic Hard Risk Gatekeeper (`src/risk/hard_gates.py`)**:
  - Mathematical firewall enforcing:
    - Max capital risk per trade: $\le 5.0\%$ of Net Liquidating Value ($5,000 max).
    - Max portfolio margin ceiling: $\le 40.0\%$ of total equity ($40,000 max).
    - Daily loss circuit breaker: $\le 5.0\%$ of day starting equity ($5,000 max loss).
    - Absolute drawdown emergency stop: $\le 10.0\%$ from peak equity ($10,000 stop).
    - Liquid DTE universe gate: 14 – 45 DTE.
    - Bid-ask slippage guard: max 3.0% of mid-price or $0.15/contract.
- **Attribution Logger (`src/core/attribution_logger.py`)**:
  - JSON-lines audit trail recording trade entries, exits, holding durations, exit reasons, and net realized P&L.
- **Submission Artifacts**:
  - Generated turnkey presentation video script (`docs/hackathon/video_script.md`) and one-page executive memo (`docs/hackathon/one_page_writeup.md`).

---

## [0.3.0] - 2026-09-02

### Added
- **Multi-Model LLM Strategist Gateway (`src/agents/strategist_agent.py`)**:
  - AsyncOpenAI client integration targeting Featherless AI serverless models (`Qwen/Qwen2.5-72B-Instruct`, `zai-org/GLM-5.2`).
  - Google GenAI SDK integration targeting Gemini 2.0 Flash (`gemini-2.0-flash`).
  - Automatic exponential backoff retry on HTTP 503 capacity errors.
  - Deterministic fallback solver for offline and mock testing.
- **Hybrid Alpaca Execution Router (`src/execution/alpaca_client.py`, `src/execution/mcp_bridge.py`)**:
  - Native `alpaca-py` v0.30+ `TradingClient` order execution (`OrderClass.MLEG`).
  - Alpaca MCP Server v2.3+ tool bridge (`get_account_info`, `get_all_positions`, `get_option_contracts`).
- **OCC 21-Character Order Builder (`src/execution/order_builder.py`)**:
  - Formatter and validator for Bull Put Credit Spreads, Bear Call Credit Spreads, and Iron Condors.

---

## [0.2.0] - 2026-09-01

### Added
- **Vectorized Greeks & Black-Scholes Engine (`src/data/greeks_engine.py`)**:
  - Analytical Black-Scholes pricing and Greeks calculation ($\Delta, \Gamma, \Theta, \nu, \rho$).
  - High-precision Implied Volatility solver using Newton-Raphson with Brent's method fallback.
- **Market Regime Detector & Volatility Edge Engine (`src/data/regime_detector.py`)**:
  - 52-week Implied Volatility Rank (IVR) and Percentile (IVP).
  - Close-to-Close and Parkinson Historical Volatility ($HV_P$).
  - Wilder's Average Directional Index (ADX 14) with $+DI / -DI$ trend filters.
  - Four-state regime classification: `HIGH_IV_TRENDING`, `HIGH_IV_RANGEBOUND`, `LOW_IV_TRENDING`, and `LOW_IV_CHOP` (Cash Preservation Mode).
- **Alpaca Real-Time Stream Client (`src/data/alpaca_stream.py`)**:
  - WebSocket market data ingestion (`OptionDataStream`, `StockDataStream`).
  - Free-tier 15-minute bar bypass via real-time quote snapshots.

---

## [0.1.0] - 2026-08-31

### Added
- **Project Genesis & Core Infrastructure**:
  - Async pub/sub Event Bus (`src/core/event_bus.py`) with topic isolation.
  - Pydantic v2 configuration settings (`src/core/config.py`) supporting dual-account routing (`test` vs `competition`).
  - Custom typed exception hierarchy (`src/core/exceptions.py`).
  - Repository Single Source of Truth (`AGENTS.md`) and Antigravity rules (`GEMINI.md`).
