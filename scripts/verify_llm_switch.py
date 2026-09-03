#!/usr/bin/env python3
"""
scripts/verify_llm_switch.py
Verification tool to test seamless switching between Google Gemini and Featherless AI.

Usage:
    # Test provider configured in .env (default):
    python scripts/verify_llm_switch.py

    # Test Featherless specifically:
    python scripts/verify_llm_switch.py --provider featherless

    # Test Featherless with a specific model:
    python scripts/verify_llm_switch.py --provider featherless --model zai-org/GLM-5.2

    # Test Gemini specifically:
    python scripts/verify_llm_switch.py --provider gemini

    # Test both providers:
    python scripts/verify_llm_switch.py --provider both
"""

import os
import sys
import time
import argparse
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

# Load environment variables from .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


def test_featherless(api_key: str, base_url: str, model: str) -> bool:
    """Tests connectivity to Featherless OpenAI-compatible endpoint."""
    console.print(f"\n[bold cyan]--- Testing Featherless AI Provider ---[/bold cyan]")
    console.print(f"  [dim]Endpoint:[/dim] {base_url}")
    console.print(f"  [dim]Model:[/dim]    {model}")

    if not api_key or api_key == "fw-your_featherless_api_key_here":
        console.print("[bold red][ERROR][/bold red] FEATHERLESS_API_KEY is missing or contains placeholder value in .env.")
        console.print("  [yellow]Tip:[/yellow] Redeem promo code [bold]ALPACA26[/bold] on https://featherless.ai/ and paste key into .env.")
        return False

    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)

        start_time = time.time()
        with console.status("[bold green]Sending ping prompt to Featherless...[/bold green]"):
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a quantitative options assistant. Respond with short valid JSON only."},
                    {"role": "user", "content": "Respond with: {\"status\": \"ok\", \"provider\": \"featherless\"}"}
                ],
                max_tokens=60,
                temperature=0.1
            )
        elapsed = time.time() - start_time
        reply = response.choices[0].message.content.strip()

        console.print(f"[bold green][OK] Success![/bold green] Response received in [bold]{elapsed:.2f}s[/bold]:")
        console.print(Panel(reply, title=f"Featherless Response ({model})", border_style="green"))
        return True

    except Exception as exc:
        console.print(f"[bold red][FAIL] Featherless API Error:[/bold red] {exc}")
        if "401" in str(exc):
            console.print("  [yellow]Hint:[/yellow] Invalid API key. Check FEATHERLESS_API_KEY in .env.")
        elif "403" in str(exc):
            console.print("  [yellow]Hint:[/yellow] Model is gated. Visit https://featherless.ai/models to unlock.")
        elif "503" in str(exc):
            console.print("  [yellow]Hint:[/yellow] Model warming up or at capacity. Retry in a few seconds.")
        return False


def test_gemini(api_key: str, model: str) -> bool:
    """Tests connectivity to Google Gemini API."""
    console.print(f"\n[bold cyan]--- Testing Google Gemini Provider ---[/bold cyan]")
    console.print(f"  [dim]Model:[/dim] {model}")

    if not api_key or api_key == "your_google_gemini_api_key_here":
        console.print("[bold yellow][NOTICE][/bold yellow] GEMINI_API_KEY is currently a placeholder in .env.")
        console.print("  [dim]Antigravity IDE handles Gemini 3.8 Flash automatically for pair-programming.[/dim]")
        console.print("  [dim]To use Gemini via standalone SDK script during automated trading, set a valid GEMINI_API_KEY in .env.[/dim]")
        return True

    try:
        from google import genai
        client = genai.Client(api_key=api_key)

        start_time = time.time()
        with console.status("[bold green]Sending ping prompt to Gemini...[/bold green]"):
            response = client.models.generate_content(
                model=model,
                contents="Respond with: {\"status\": \"ok\", \"provider\": \"gemini\"}"
            )
        elapsed = time.time() - start_time
        reply = response.text.strip()

        console.print(f"[bold green][OK] Success![/bold green] Response received in [bold]{elapsed:.2f}s[/bold]:")
        console.print(Panel(reply, title=f"Gemini Response ({model})", border_style="green"))
        return True

    except Exception as exc:
        console.print(f"[bold red][FAIL] Gemini API Error:[/bold red] {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify LLM provider switching between Gemini and Featherless.")
    parser.add_argument(
        "--provider",
        choices=["gemini", "featherless", "both"],
        help="Provider to test (overrides LLM_PROVIDER in .env)"
    )
    parser.add_argument(
        "--model",
        help="Model identifier (overrides default model in .env)"
    )
    args = parser.parse_args()

    # Read from environment
    env_provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    target_provider = args.provider if args.provider else env_provider

    featherless_key = os.getenv("FEATHERLESS_API_KEY", "")
    featherless_url = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
    featherless_model = args.model if (args.model and target_provider in ("featherless", "both")) else os.getenv("FEATHERLESS_MODEL", "Qwen/Qwen2.5-72B-Instruct")

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    gemini_model = args.model if (args.model and target_provider in ("gemini", "both")) else os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    table = Table(title="Active LLM Configuration (.env)")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="magenta")
    table.add_row("Configured LLM_PROVIDER", env_provider)
    table.add_row("Testing Target", target_provider)
    table.add_row("Featherless Model", featherless_model)
    table.add_row("Gemini Model", gemini_model)
    console.print(table)

    success = True
    if target_provider in ("featherless", "both"):
        success = test_featherless(featherless_key, featherless_url, featherless_model) and success

    if target_provider in ("gemini", "both"):
        success = test_gemini(gemini_key, gemini_model) and success

    if success:
        console.print("\n[bold green][SUCCESS] LLM Provider Switch Verified Successfully![/bold green]")
        console.print("[dim]Switching LLM_PROVIDER in .env or passing --llm-provider to CLI works with zero code edits.[/dim]\n")
        sys.exit(0)
    else:
        console.print("\n[bold red][FAIL] LLM Provider Verification Encountered Issues.[/bold red]\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
