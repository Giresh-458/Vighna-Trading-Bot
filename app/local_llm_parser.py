import re
import json
import urllib.request
import logging

logger = logging.getLogger(__name__)

class LocalLLMParser:
    """
    Parses unstructured replies using a local LLM (e.g. Ollama) or Regex fallback.
    """
    OLLAMA_URL = "http://localhost:11434/api/generate"
    MODEL_NAME = "qwen3:8b" # Used available local model

    @classmethod
    def parse_reply(cls, text: str) -> dict:
        """
        Parses a reply message and returns an action dictionary.
        Returns: {"action": "EXIT_MARKET" | "MODIFY_TARGET" | "MODIFY_SL" | "CANCEL" | "IGNORE", "price": float or null}
        """
        # 1. Try Local LLM (Ollama)
        try:
            prompt = f"""You are a trading assistant processing a Telegram reply to an active trade signal.
Read the user's reply and determine their intent.

Actions:
1. EXIT_MARKET: They want to exit the open position immediately at the current market price ("exit now", "book full profit").
2. MODIFY_TARGET: They want to change the target limit price to a specific number ("exit at 85", "new target 100", "book at 90").
3. MODIFY_SL: They want to change the stop loss to a specific number ("trail sl to 40", "safe traders sl 30").
4. CANCEL: They want to cancel a pending entry that hasn't executed yet ("ignore this call", "cancel trade").
5. IGNORE: General commentary, no action needed.

Reply: "{text}"

You must return strictly a JSON object with this format:
{{
    "action": "EXIT_MARKET" | "MODIFY_TARGET" | "MODIFY_SL" | "CANCEL" | "IGNORE",
    "price": <float or null>
}}
"""
            
            data = json.dumps({
                "model": cls.MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "format": "json"
            }).encode('utf-8')
            
            req = urllib.request.Request(cls.OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5.0) as response:
                result = json.loads(response.read().decode('utf-8'))
                response_text = result.get('response', '{}')
                parsed_json = json.loads(response_text)
                action = parsed_json.get('action', 'IGNORE').upper()
                price = parsed_json.get('price', None)
                if price is not None:
                    try: price = float(price)
                    except: price = None
                if action in ["EXIT_MARKET", "MODIFY_TARGET", "MODIFY_SL", "CANCEL"]:
                    return {"action": action, "price": price}
        except Exception as e:
            logger.debug(f"Local LLM failed or not available, falling back to regex: {e}")
            
        # 2. Fallback to Regex
        text_lower = text.lower()
        
        # Look for explicit numbers in target/sl phrases
        target_match = re.search(r'(?:exit at|target|book at)\s*(\d+(?:\.\d+)?)', text_lower)
        sl_match = re.search(r'(?:sl|stoploss|stop loss).*?(\d+(?:\.\d+)?)', text_lower)
        
        if target_match:
            return {"action": "MODIFY_TARGET", "price": float(target_match.group(1))}
        elif sl_match:
            return {"action": "MODIFY_SL", "price": float(sl_match.group(1))}
        elif re.search(r'\b(exit|book|close|square\s*off)\b', text_lower):
            return {"action": "EXIT_MARKET", "price": None}
        elif re.search(r'\b(cancel|ignore)\b', text_lower):
            return {"action": "CANCEL", "price": None}
            
        return {"action": "IGNORE", "price": None}
