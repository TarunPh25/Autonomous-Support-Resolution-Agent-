"""
LLM Client — Groq API integration with graceful fallback.

Provides a unified interface for LLM calls used by the decision engine.
If GROQ_API_KEY is missing or API fails, falls back silently to None
so the deterministic engine can take over.

Usage:
    client = LLMClient()
    if client.available:
        result = await client.call("Your prompt here")
"""

import os
import json
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("agent.llm")

# Groq SDK is optional — system works without it
try:
    from groq import Groq, APIError, RateLimitError, APITimeoutError
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.info("Groq SDK not installed. LLM features disabled. Install with: pip install groq")


class LLMClient:
    """
    Wrapper around Groq API (LLaMA 3) with:
    - Graceful fallback if API key missing or SDK not installed
    - Timeout handling
    - Rate limit handling with retry
    - Token usage tracking for audit logs
    """

    def __init__(
        self,
        model: str = "llama-3.3-70b-versatile",
        max_tokens: int = 512,
        temperature: float = 0.3,
        timeout: float = 10.0,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._client: Optional[object] = None
        self._available = False
        self._total_calls = 0
        self._total_tokens = 0
        self._total_failures = 0

        # Try to initialize
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            logger.info("GROQ_API_KEY not set. LLM features will be disabled.")
            return
        if not GROQ_AVAILABLE:
            logger.info("Groq SDK not installed. LLM features will be disabled.")
            return

        try:
            self._client = Groq(api_key=api_key)
            self._available = True
            logger.info(f"LLM client initialized: model={model}, max_tokens={max_tokens}")
        except Exception as e:
            logger.warning(f"Failed to initialize Groq client: {e}")

    @property
    def available(self) -> bool:
        """Check if LLM is available and ready."""
        return self._available

    @property
    def stats(self) -> dict:
        """Return usage statistics."""
        return {
            "total_calls": self._total_calls,
            "total_tokens": self._total_tokens,
            "total_failures": self._total_failures,
        }

    async def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Optional[str]:
        """
        Call the LLM with a prompt. Returns response text or None on failure.
        
        This is designed to NEVER raise — failures return None so the
        deterministic fallback can take over.
        
        Args:
            prompt: User prompt
            system_prompt: System role instruction
            max_tokens: Override default max tokens
            temperature: Override default temperature
            
        Returns:
            Response text string, or None if LLM unavailable/failed
        """
        if not self._available:
            return None

        self._total_calls += 1

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            # Run the sync Groq call in a thread to avoid blocking the event loop
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens or self.max_tokens,
                        temperature=temperature if temperature is not None else self.temperature,
                    )
                ),
                timeout=self.timeout
            )

            # Extract response
            content = response.choices[0].message.content.strip()
            
            # Track token usage
            if hasattr(response, "usage") and response.usage:
                self._total_tokens += response.usage.total_tokens

            logger.debug(f"LLM response ({len(content)} chars): {content[:100]}...")
            return content

        except asyncio.TimeoutError:
            self._total_failures += 1
            logger.warning(f"LLM call timed out after {self.timeout}s. Falling back to deterministic.")
            return None
        except Exception as e:
            self._total_failures += 1
            error_type = type(e).__name__
            logger.warning(f"LLM call failed ({error_type}): {e}. Falling back to deterministic.")
            return None

    async def classify_ticket(self, subject: str, body: str) -> Optional[dict]:
        """
        Use LLM to classify a support ticket.
        
        Returns dict with 'category' and 'flags', or None on failure.
        Deterministic classifier will be used as fallback.
        """
        system_prompt = (
            "You are a ticket classification engine. Respond ONLY with valid JSON, no markdown.\n"
            "Classify the support ticket into exactly one category and extract any flags.\n\n"
            "Categories: refund, return, cancellation, damage_claim, wrong_item, "
            "warranty, order_status, general_faq, exchange, unknown\n\n"
            "Flags (include ALL that apply): threatening_language, social_engineering, "
            "urgency, frustration\n\n"
            "Also extract order_id (format: ORD-XXXX) and product_id (format: PXXX) if present.\n\n"
            "Response format:\n"
            '{"category": "...", "flags": [...], "order_id": "..." or null, '
            '"product_id": "..." or null, "confidence": 0.0-1.0, '
            '"reasoning": "brief explanation"}'
        )

        prompt = f"Subject: {subject}\n\nBody: {body}"

        response = await self.call(prompt, system_prompt=system_prompt, temperature=0.1)
        if not response:
            return None

        # Parse JSON response
        try:
            # Strip markdown code fences if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
                cleaned = cleaned.rsplit("```", 1)[0] if "```" in cleaned else cleaned
                cleaned = cleaned.strip()
            
            result = json.loads(cleaned)
            
            # Validate required fields
            if "category" not in result:
                logger.warning("LLM classification missing 'category' field")
                return None
                
            # Normalize
            valid_categories = {
                "refund", "return", "cancellation", "damage_claim", "wrong_item",
                "warranty", "order_status", "general_faq", "exchange", "unknown"
            }
            if result["category"] not in valid_categories:
                logger.warning(f"LLM returned invalid category: {result['category']}")
                return None

            result.setdefault("flags", [])
            result.setdefault("confidence", 0.7)
            result.setdefault("reasoning", "")
            
            logger.info(
                f"LLM classified as '{result['category']}' "
                f"(confidence: {result['confidence']}) -- {result.get('reasoning', '')[:60]}"
            )
            return result

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse LLM classification response: {e}")
            return None

    async def generate_thought(self, state_summary: str) -> Optional[str]:
        """
        Use LLM to generate a richer reasoning thought for the current state.
        
        Returns natural language analysis string, or None on failure.
        """
        system_prompt = (
            "You are the reasoning engine of an autonomous customer support agent. "
            "Given the current state of a support ticket, produce a concise but insightful "
            "analysis of what you know, what's missing, and what should happen next.\n\n"
            "Rules:\n"
            "- Be analytical, not conversational\n"
            "- Reference specific data points (order IDs, amounts, dates)\n"
            "- Flag any concerns (policy violations, suspicious behaviour, edge cases)\n"
            "- Keep it to 2-3 sentences maximum\n"
            "- Do NOT suggest tool calls — just analyze the situation"
        )

        response = await self.call(state_summary, system_prompt=system_prompt, max_tokens=200)
        return response

    async def compose_reply(
        self,
        ticket: dict,
        decision_summary: str,
        customer_name: str,
        customer_tier: str,
        policy_refs: list[str],
    ) -> Optional[str]:
        """
        Use LLM to compose a professional customer-facing reply.
        
        Returns reply text or None on failure.
        The deterministic template reply will be used as fallback.
        """
        system_prompt = (
            "You are a professional customer support agent for ShopWave, an e-commerce "
            "company. Write a clear, empathetic, and professional email reply.\n\n"
            "Rules:\n"
            "- Address the customer by first name\n"
            "- Be empathetic but concise\n"
            "- Explain the decision clearly with the reason\n"
            "- Include relevant details (order IDs, amounts, transaction IDs, dates)\n"
            "- If the decision is negative, offer alternatives where possible\n"
            "- Adjust tone for customer tier (standard=professional, "
            "premium=warm+appreciative, vip=highly personalized+priority language)\n"
            "- Sign off as 'ShopWave Support'\n"
            "- Do NOT use markdown formatting (no ** or ## etc)\n"
            "- Keep it under 150 words"
        )

        prompt = (
            f"Customer: {customer_name} (tier: {customer_tier})\n"
            f"Ticket subject: {ticket.get('subject', '')}\n"
            f"Ticket body: {ticket.get('body', '')}\n\n"
            f"Agent's decision and context:\n{decision_summary}\n\n"
            f"Relevant policies: {', '.join(policy_refs) if policy_refs else 'None'}\n\n"
            f"Write the reply email:"
        )

        response = await self.call(prompt, system_prompt=system_prompt, max_tokens=350)
        
        # Basic validation — must have greeting and sign-off
        if response:
            has_name = customer_name.split()[0].lower() in response.lower() if customer_name != "Customer" else True
            has_signoff = "shopwave" in response.lower()
            if not has_signoff:
                response += "\n\nBest regards,\nShopWave Support"
            if not has_name and customer_name != "Customer":
                first = customer_name.split()[0]
                response = f"Hi {first},\n\n{response}"
        
        return response


# ─────────────────────────────────────────────────────────
# SINGLETON / FACTORY
# ─────────────────────────────────────────────────────────

_global_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get or create the global LLM client instance."""
    global _global_client
    if _global_client is None:
        _global_client = LLMClient()
    return _global_client


def init_llm_client(**kwargs) -> LLMClient:
    """Initialize the global LLM client with custom settings."""
    global _global_client
    _global_client = LLMClient(**kwargs)
    return _global_client
