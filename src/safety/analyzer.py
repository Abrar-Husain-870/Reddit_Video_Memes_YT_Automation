import json
import re
import time
import traceback
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import config
from src.logger import logger
from src.reddit.models import RedditPost

# Risk levels map for comparisons
RISK_LEVELS = {
    "safe": 0,
    "low risk": 1,
    "medium risk": 2,
    "high risk": 3,
    "reject": 4
}

class ContentSafetyAnalyzer:
    """
    Dedicated ContentSafetyAnalyzer to inspect Reddit ingestion posts, generated scripts,
    narration, metadata, and final upload parameters before publishing.
    """
    
    def __init__(self) -> None:
        # Compile local regex filters for the 14 prohibited categories
        self.local_rules = {
            "sexual_assault": re.compile(
                r"\b(rape|raped|raping|rapist|rapists|sexual assault|sexually assaulted|sexual abuse|molest|molested|molesting|molestation|groped|groping|non-consensual sex)\b",
                re.IGNORECASE
            ),
            "child_exploitation": re.compile(
                r"\b(pedophile|pedophilia|pedophiles|child abuse|child exploitation|underage sex|grooming child|groomer|groomers|cp|csae|csam)\b",
                re.IGNORECASE
            ),
            "suicide_or_self_harm": re.compile(
                r"\b(suicide|suicidal|kill myself|kill yourself|end my life|ended her life|ended his life|ended my life|slit my wrist|slit wrists|slitting wrists|cutting myself|cut myself|hang myself|hanged himself|hanged herself|overdose to die)\b",
                re.IGNORECASE
            ),
            "graphic_violence": re.compile(
                r"\b(gore|decapitated|decapitation|dismembered|dismemberment|slaughtered|bloodbath|tortured|torturing|mutilated|mutilation|gory|blood and guts)\b",
                re.IGNORECASE
            ),
            "murder": re.compile(
                r"\b(murder|murdered|murdering|murderer|murders|stabbed to death|shot to death|strangled to death|assassinated|assassination|serial killer)\b",
                re.IGNORECASE
            ),
            "terrorism_or_extremist": re.compile(
                r"\b(terrorism|terrorist|terrorists|isis|al-qaeda|jihadist|jihadi|taliban|extremist group|extremism|pipe bomb|suicide bomber|mass shooter|mass shooting)\b",
                re.IGNORECASE
            ),
            "hate_speech": re.compile(
                r"\b(faggot|kike|retard|spic|cunt|chink|nigger|nigga|tranny|dyke|shemale|wetback|coon|raghead|gook)\b",
                re.IGNORECASE
            ),
            "illegal_drugs": re.compile(
                r"\b(cocaine|heroin|methamphetamine|fentanyl|drug dealer|selling weed|grow weed|manufacturing meth|cook meth|drug cartel|drug smuggling|dealer)\b",
                re.IGNORECASE
            ),
            "explicit_sexual": re.compile(
                r"\b(porn|pornography|orgasm|orgasms|erotic|hooker|escorts|escort|prostitute|prostitutes|prostitution|intercourse|nsfw|threesome|masturbate|masturbating|blowjob|handjob|cuckold|milf|hentai|incest)\b",
                re.IGNORECASE
            ),
            "criminal_instructions": re.compile(
                r"\b(how to steal|how to hack|how to make a bomb|how to shoplift|shoplifting|steal cars|stealing cars|hack into|carding tutorial|credit card fraud tutorial)\b",
                re.IGNORECASE
            ),
            "dangerous_challenges": re.compile(
                r"\b(tide pod challenge|blue whale challenge|dangerous challenge|choking game|blackout challenge|fire challenge)\b",
                re.IGNORECASE
            ),
            "harassment": re.compile(
                r"\b(doxxed|doxx|doxing|harass|harassed|harassing|harassment|bully|bullied|bullying|blackmail|blackmailed|blackmailing|extort|extorted|extortion|revenge porn)\b",
                re.IGNORECASE
            ),
            "doxxing": re.compile(
                r"\b(social security number|ssn|home address is|phone number is|ip address is|identity theft|leak address|leak phone number)\b",
                re.IGNORECASE
            ),
            "fraud_and_scams": re.compile(
                r"\b(ponzi|scam|scammed|scammer|scams|phishing|carding|hack account|steal money|crypto scam|free money hack|pyramid scheme|get rich quick scam)\b",
                re.IGNORECASE
            )
        }

    def run_local_scan(self, text: str) -> Tuple[str, List[str], str]:
        """
        Fast local regex rule-based scanner for prohibited categories.
        Returns: Tuple (risk_score, categories_detected, reason)
        """
        detected = []
        for category, pattern in self.local_rules.items():
            matches = pattern.findall(text)
            if matches:
                detected.append(category)
        
        if detected:
            # If any prohibited categories are explicitly matched, default to Reject
            return "Reject", detected, f"Local safety regex match: {', '.join(detected)}"
        
        return "Safe", [], ""

    def _call_llm(self, system_prompt: str, user_prompt: str, image_path: Optional[Path] = None) -> str:
        """Call the configured LLM provider for content safety classification."""
        orig_provider = config.LLM_PROVIDER.lower()
        orig_model_name = config.LLM_MODEL
        
        # If performing a vision check and Gemini key is configured, route to Gemini as it supports vision on the free tier
        if image_path and config.GEMINI_API_KEY:
            provider = "gemini"
            model_name = "gemini-1.5-flash"
        else:
            provider = orig_provider
            model_name = orig_model_name
        
        try:
            return self._call_llm_internal(provider, model_name, system_prompt, user_prompt, image_path)
        except Exception as e:
            if image_path:
                logger.warning(f"Safety check with image failed ({e}). Falling back to text-only safety scan.")
                return self._call_llm_internal(orig_provider, orig_model_name, system_prompt, user_prompt, None)
            else:
                raise e

    def _call_llm_internal(self, provider: str, model_name: str, system_prompt: str, user_prompt: str, image_path: Optional[Path] = None) -> str:
        if provider == "groq":
            from groq import Groq
            if not config.GROQ_API_KEY:
                raise ValueError("GROQ_API_KEY is not configured")
            client = Groq(api_key=config.GROQ_API_KEY)
            
            actual_model = model_name
            if image_path:
                import base64
                with open(image_path, "rb") as img_f:
                    b64_img = base64.b64encode(img_f.read()).decode('utf-8')
                user_content = [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_img}"
                        }
                    }
                ]
                # Check if model is vision-capable; if not, fallback to Llama 4 Scout
                is_vision_model = any(k in model_name.lower() for k in ["vision", "scout", "qwen3.6-27b"])
                if not is_vision_model:
                    actual_model = "meta-llama/llama-4-scout-17b-16e-instruct"
            else:
                user_content = user_prompt
                
            completion = client.chat.completions.create(
                model=actual_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=300,
                timeout=15,
            )
            return completion.choices[0].message.content or ""
            
        elif provider in ("openai", "deepseek", "openrouter", "ollama"):
            import openai
            api_key = ""
            base_url = None
            if provider == "openai":
                api_key = config.OPENAI_API_KEY
            elif provider == "deepseek":
                api_key = config.DEEPSEEK_API_KEY
                base_url = "https://api.deepseek.com"
            elif provider == "openrouter":
                api_key = config.OPENROUTER_API_KEY
                base_url = "https://openrouter.ai/api/v1"
            elif provider == "ollama":
                api_key = "ollama"
                base_url = config.OLLAMA_API_URL
                
            if not api_key and provider != "ollama":
                raise ValueError(f"{provider.upper()} API key is not configured")
                
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            
            if image_path:
                import base64
                with open(image_path, "rb") as img_f:
                    b64_img = base64.b64encode(img_f.read()).decode('utf-8')
                user_content = [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_img}"
                        }
                    }
                ]
            else:
                user_content = user_prompt

            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=300,
                timeout=15,
            )
            return completion.choices[0].message.content or ""
            
        elif provider == "gemini":
            import google.generativeai as genai
            if not config.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY is not configured")
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt
            )
            contents = [user_prompt]
            if image_path:
                from PIL import Image
                img = Image.open(image_path)
                contents.append(img)
            response = model.generate_content(
                contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=300,
                )
            )
            return response.text or ""
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    def run_llm_scan(self, text: str, image_path: Optional[Path] = None) -> Tuple[str, List[str], str]:
        """
        Query LLM for contextual classification of content safety.
        Returns: Tuple (risk_score, categories_detected, reason)
        """
        system_prompt = (
            "You are an expert content safety classification assistant for a video sharing platform.\n"
            "Your task is to analyze the provided text and image (if provided) and determine if it violates YouTube Community Guidelines or advertiser-friendly content policies.\n\n"
            "You MUST evaluate the content for the following 14 prohibited categories:\n"
            "1. Sexual assault\n"
            "2. Child exploitation or abuse\n"
            "3. Suicide or self-harm\n"
            "4. Graphic violence\n"
            "5. Murder\n"
            "6. Terrorism or extremist content\n"
            "7. Hate speech\n"
            "8. Illegal drug production, promotion, or sales\n"
            "9. Explicit sexual content\n"
            "10. Criminal instructions\n"
            "11. Dangerous challenges\n"
            "12. Harassment\n"
            "13. Doxxing or personal information\n"
            "14. Fraud and scams\n\n"
            "Do not rely on simple keyword matching. Analyze the context, tone, and intent of the overall text. Even if a word is not explicitly present, flag it if the context strongly references or details any of these topics.\n\n"
            "Based on your analysis, assign one of the following risk scores:\n"
            "- Safe: Content is family-friendly, positive, educational, or completely harmless.\n"
            "- Low Risk: Content contains minor conflict, relationship issues, mild jokes, or common life discussions that are safe for advertising.\n"
            "- Medium Risk: Content discusses sensitive topics in a non-graphic, news-reporting, or educational style. Possibly sensitive but not violating.\n"
            "- High Risk: Content contains references to violence, drug usage, explicit harassment, or borderline violations. Not suitable for automated publishing without human review.\n"
            "- Reject: Content clearly or severely violates safety policies (e.g., suicide detail, graphic murder, hate speech, explicit sexual content, child abuse).\n\n"
            "You must respond ONLY with a raw JSON object containing these keys. Do not include markdown codeblocks (no ```json or similar tags):\n"
            "{\n"
            "  \"risk_score\": \"Safe\" | \"Low Risk\" | \"Medium Risk\" | \"High Risk\" | \"Reject\",\n"
            "  \"categories_detected\": [\"category1\", \"category2\", ...],\n"
            "  \"reason\": \"Detailed explanation of the risk classification\"\n"
            "}"
        )
        
        user_prompt = f"Text to analyze:\n{text}"
        
        try:
            response_text = self._call_llm(system_prompt, user_prompt, image_path)
            # Clean response text in case it wrapped in markdown backticks
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                # strip opening
                cleaned = re.sub(r"^```[a-zA-Z0-9]*\n", "", cleaned)
                # strip closing
                cleaned = re.sub(r"\n```$", "", cleaned)
                cleaned = cleaned.strip()
            
            # Find boundaries of JSON
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                cleaned = cleaned[start:end+1]
                
            data = json.loads(cleaned)
            risk_score = data.get("risk_score", "Reject")
            categories = data.get("categories_detected", [])
            reason = data.get("reason", "No reason provided by LLM safety scanner.")
            return risk_score, categories, reason
        except Exception as e:
            logger.warning(f"LLM content safety check failed: {e}. Traceback: {traceback.format_exc()}")
            raise e

    def check_safety(
        self,
        title: str = "",
        body: str = "",
        narration: str = "",
        yt_title: str = "",
        description: str = "",
        tags: List[str] = None,
        hashtags: List[str] = None,
        captions: str = "",
        metadata: dict = None,
        image_path: Optional[Path] = None,
        stage: str = "Ingestion"
    ) -> Dict[str, any]:
        """
        Evaluates content safety against policies.
        Runs local and contextual checks, then validates against safety mode policies.
        """
        if not config.ENABLE_CONTENT_SAFETY:
            logger.info("Content safety checks are disabled.")
            return {"passed": True, "risk_score": "Safe", "categories_detected": [], "reason": "Content safety checks disabled."}

        import subprocess
        temp_image_path = None
        
        try:
            if image_path and image_path.exists():
                if image_path.suffix.lower() in ('.mp4', '.webm', '.gif'):
                    temp_image_path = image_path.parent / "temp_safety_frame.png"
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", "00:00:01",
                        "-i", str(image_path),
                        "-vframes", "1",
                        str(temp_image_path)
                    ]
                    try:
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
                        image_path = temp_image_path
                    except Exception as e:
                        logger.warning(f"Frame extraction failed for video safety check: {e}. Falling back to text-only safety scan.")
                        image_path = None

            # Assemble text representation
            text_parts = []
            if title:
                text_parts.append(f"Reddit Title: {title}")
            if body:
                text_parts.append(f"Reddit Body: {body}")
            if narration:
                text_parts.append(f"Narration: {narration}")
            if yt_title:
                text_parts.append(f"YouTube Title: {yt_title}")
            if description:
                text_parts.append(f"YouTube Description: {description}")
            if tags:
                text_parts.append(f"Tags: {', '.join(tags)}")
            if hashtags:
                text_parts.append(f"Hashtags: {', '.join(hashtags)}")
            if captions:
                text_parts.append(f"Captions: {captions}")
            if metadata:
                text_parts.append(f"Metadata: {json.dumps(metadata)}")

            full_text = "\n\n".join(text_parts).strip()
            if not full_text and not image_path:
                return {"passed": True, "risk_score": "Safe", "categories_detected": [], "reason": "No content to check."}

            logger.info(f"Running content safety analysis (Stage: {stage})...")

            # ── 1. Local Regex Scanner (Fast pass) ────────────────────────────────
            local_score, local_cats, local_reason = self.run_local_scan(full_text)
            
            # ── 2. LLM Contextual Safety Scanner ──────────────────────────────────
            risk_score = "Safe"
            categories_detected = []
            reason = ""
            llm_success = False

            try:
                risk_score, categories_detected, reason = self.run_llm_scan(full_text, image_path)
                llm_success = True
                logger.info(f"LLM safety check succeeded. Risk: {risk_score}, Categories: {categories_detected}")
            except Exception as e:
                logger.error(f"Contextual LLM safety scan error: {e}")
                if config.SAFETY_MODE == "strict":
                    # Strict mode is fail-safe, default to Reject on error
                    risk_score = "Reject"
                    categories_detected = ["llm_api_failure"]
                    reason = f"Strict mode fail-safe: Contextual safety check API failed/timed out. Error: {str(e)}"
                    logger.warning("Failing safe: LLM check failed in strict safety mode. Rejecting content.")
                else:
                    # Lenient or Standard: fallback to local regex scan results
                    risk_score = local_score
                    categories_detected = local_cats
                    reason = f"LLM failed, fell back to local scan. {local_reason}"
                    logger.info("Fell back to local safety scan because LLM check failed.")

            # If LLM check succeeded, but local scan flagged a critical term, merge them for extra protection
            if llm_success and local_score == "Reject":
                logger.warning(f"Local scan flagged 'Reject' content, overriding LLM risk score '{risk_score}'.")
                risk_score = "Reject"
                categories_detected = list(set(categories_detected + local_cats))
                reason = f"Local regex override: {local_reason}. (LLM reported: {reason})"

            # ── 3. Policy Enforcement / Threshold Validation ──────────────────────
            # Map string risk score to integer levels
            score_val = RISK_LEVELS.get(risk_score.lower(), 4)
            
            # Determine maximum allowed risk
            max_allowed_risk = config.MAX_ALLOWED_RISK.lower()
            max_allowed_val = RISK_LEVELS.get(max_allowed_risk, 1) # default to low risk
            
            # Default rejection logic: High Risk and Reject are always blocked
            # Also reject if it exceeds max allowed risk from config
            is_rejected = (score_val >= 3) or (score_val > max_allowed_val)
            
            passed = not is_rejected

            logger.info(f"Safety check results (Stage: {stage}): Passed={passed}, Risk={risk_score}, AllowedMax={config.MAX_ALLOWED_RISK}")

            return {
                "passed": passed,
                "risk_score": risk_score,
                "categories_detected": categories_detected,
                "reason": reason
            }
        finally:
            if temp_image_path and temp_image_path.exists():
                try:
                    temp_image_path.unlink()
                except Exception:
                    pass

    def check_meme_suitability(self, title: str, image_path: Optional[Path] = None) -> Dict[str, any]:
        """
        Evaluates a meme's suitability and appeal using LLM.
        Rates: humor, simplicity, universal_appeal, family_friendly, requires_background_knowledge.
        """
        import subprocess
        temp_image_path = None
        
        try:
            if image_path and image_path.exists():
                if image_path.suffix.lower() in ('.mp4', '.webm', '.gif'):
                    temp_image_path = image_path.parent / "temp_suitability_frame.png"
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", "00:00:01",
                        "-i", str(image_path),
                        "-vframes", "1",
                        str(temp_image_path)
                    ]
                    try:
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
                        image_path = temp_image_path
                    except Exception as e:
                        logger.warning(f"Frame extraction failed for suitability check: {e}. Falling back to text-only suitability check.")
                        image_path = None

            system_prompt = (
                "You are an expert meme evaluation assistant.\n"
                "Your task is to analyze the provided meme image and/or title, and evaluate its humor, simplicity, universal appeal, family friendliness, background knowledge requirements, and presence of watermarks.\n\n"
                "Meme Selection Criteria:\n"
                "- Humor: How funny is the meme? (1-10)\n"
                "- Simplicity: How simple is the meme to understand? (1-10) High score (8-10) means it can be understood in 2-3 seconds. Low score (1-5) means it is complex, wordy, or confusing.\n"
                "- Universal Appeal: How relatable is this meme to a general broad audience (teenagers to adults)? (1-10) High score (8-10) means it is about daily life, animals, food, school, or cartoons. Low score (1-5) means it is about politics, religion, programming, finance, history, philosophy, or specific local/regional context.\n"
                "- Family Friendliness: Does the meme avoid NSFW content, politics, religion, offensive humor, drugs, violence, hate speech, or YouTube policy violations? (Pass/Fail)\n"
                "- Requires Background Knowledge: Does the viewer need specialized knowledge (e.g. software development, advanced math, specific crypto coins, complex history, deep Reddit lore, or specific anime/niche games) to understand the joke? (Yes/No)\n"
                "- Has Watermark: Does the image contain any social media watermarks or logos (e.g. TikTok, Instagram, CapCut, or other video editing/sharing app logos or username overlays)? (Yes/No)\n\n"
                "You must respond ONLY with a raw JSON object containing these keys. Do not include markdown codeblocks (no ```json or similar tags):\n"
                "{\n"
                "  \"humor\": 1-10,\n"
                "  \"simplicity\": 1-10,\n"
                "  \"universal_appeal\": 1-10,\n"
                "  \"family_friendly\": \"Pass\" or \"Fail\",\n"
                "  \"requires_background_knowledge\": \"Yes\" or \"No\",\n"
                "  \"has_watermark\": \"Yes\" or \"No\",\n"
                "  \"reason\": \"A brief 1-sentence explanation of your rating.\"\n"
                "}"
            )
            
            user_prompt = f"Meme Title: {title}"
            
            try:
                response_text = self._call_llm(system_prompt, user_prompt, image_path)
                cleaned = response_text.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```[a-zA-Z0-9]*\n", "", cleaned)
                    cleaned = re.sub(r"\n```$", "", cleaned)
                    cleaned = cleaned.strip()
                
                start = cleaned.find("{")
                end = cleaned.rfind("}")
                if start != -1 and end != -1:
                    cleaned = cleaned[start:end+1]
                    
                data = json.loads(cleaned)
                
                humor = int(data.get("humor", 0))
                simplicity = int(data.get("simplicity", 0))
                universal_appeal = int(data.get("universal_appeal", 0))
                family_friendly = data.get("family_friendly", "").strip().lower()
                requires_bk = data.get("requires_background_knowledge", "").strip().lower()
                has_watermark = data.get("has_watermark", "").strip().lower()
                
                passed = (
                    humor >= 7 and
                    simplicity >= 8 and
                    universal_appeal >= 8 and
                    family_friendly == "pass" and
                    requires_bk == "no"
                )
                
                if config.REJECT_WATERMARKS and has_watermark == "yes":
                    passed = False
                    logger.warning("Meme rejected because a watermark was detected.")
                
                logger.info(
                    f"Meme suitability evaluation: passed={passed} | "
                    f"humor={humor}/10, simplicity={simplicity}/10, universal_appeal={universal_appeal}/10, "
                    f"family_friendly={family_friendly}, requires_bk={requires_bk}, has_watermark={has_watermark}"
                )
                
                return {
                    "passed": passed,
                    "ratings": data
                }
            except Exception as e:
                logger.warning(f"LLM meme suitability rating failed: {e}. Defaulting to Pass to avoid blocker.")
                return {
                    "passed": True,
                    "ratings": {
                        "humor": 7,
                        "simplicity": 8,
                        "universal_appeal": 8,
                        "family_friendly": "Pass",
                        "requires_background_knowledge": "No",
                        "reason": f"Meme suitability rating failed, passed by default. Error: {str(e)}"
                    }
                }
        finally:
            if temp_image_path and temp_image_path.exists():
                try:
                    temp_image_path.unlink()
                except Exception:
                    pass

    def check_female_presence(self, image_path: Path) -> bool:
        """
        Extracts key frames from the video and uses a vision-capable LLM to detect
        if there is a female human in the video.
        Returns: True if a female human is detected, False otherwise.
        """
        if not image_path or not image_path.exists():
            return False
            
        import subprocess
        
        # Calculate video duration if possible to choose representative timestamps
        video_dur = 10.0
        try:
            from src.video.renderer import _get_audio_duration
            video_dur = _get_audio_duration(image_path)
        except Exception:
            pass
            
        # We will extract up to two frames to scan: one near the start (2s or 20% mark)
        # and one near the middle (50% mark).
        timestamps = [min(2.0, video_dur * 0.2), video_dur * 0.5]
        
        for i, ts in enumerate(timestamps):
            temp_frame_path = image_path.parent / f"temp_female_check_frame_{i}.png"
            
            # Format timestamp as HH:MM:SS
            h = int(ts // 3600)
            m = int((ts % 3600) // 60)
            s = ts % 60
            ts_str = f"{h:02d}:{m:02d}:{s:05.2f}"
            
            cmd = [
                "ffmpeg", "-y",
                "-ss", ts_str,
                "-i", str(image_path),
                "-vframes", "1",
                str(temp_frame_path)
            ]
            
            extracted = False
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
                extracted = True
            except Exception as e:
                logger.warning(f"Frame extraction failed at {ts_str} for female presence check: {e}")
                
            if not extracted or not temp_frame_path.exists():
                continue
                
            try:
                system_prompt = (
                    "You are an AI assistant specialized in visual content analysis.\n"
                    "Your task is to analyze the image and determine if it contains any female human (woman, girl, female child/infant, or female adult).\n"
                    "Respond ONLY with a JSON object in this format:\n"
                    "{\n"
                    "  \"contains_female_human\": true | false,\n"
                    "  \"confidence\": 0.0 - 1.0,\n"
                    "  \"reason\": \"Brief explanation of what was detected\"\n"
                    "}"
                )
                user_prompt = "Does this image contain any female human?"
                
                response_text = self._call_llm(system_prompt, user_prompt, temp_frame_path)
                cleaned = response_text.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```[a-zA-Z0-9]*\n", "", cleaned)
                    cleaned = re.sub(r"\n```$", "", cleaned)
                    cleaned = cleaned.strip()
                    
                start = cleaned.find("{")
                end = cleaned.rfind("}")
                if start != -1 and end != -1:
                    cleaned = cleaned[start:end+1]
                    
                data = json.loads(cleaned)
                contains_female = bool(data.get("contains_female_human", False))
                reason = data.get("reason", "No reason provided")
                logger.info(f"Female presence check (Frame {i} at {ts_str}): contains_female={contains_female} | Reason: {reason}")
                
                if contains_female:
                    return True
            except Exception as e:
                logger.warning(f"Failed to query LLM for female presence check at {ts_str}: {e}")
            finally:
                if temp_frame_path.exists():
                    try:
                        temp_frame_path.unlink()
                    except Exception:
                        pass
                        
        return False

def log_rejected_post(post_id: str, subreddit: str, risk_score: str, category: str, reason: str) -> None:
    """Logs rejected post details to rejected_posts.json to ensure they are never retried."""
    rejected_file = config.DB_DIR / "rejected_posts.json"
    
    new_entry = {
        "reddit_id": post_id,
        "subreddit": subreddit,
        "risk_score": risk_score,
        "detection_category": category,
        "reason": reason,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    
    # Read existing entries
    entries = []
    if rejected_file.exists():
        try:
            with open(rejected_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    entries = data
        except Exception as e:
            logger.warning(f"Failed to read rejected posts JSON: {e}")
            
    # Check if already exists to avoid duplicates
    if not any(e.get("reddit_id") == post_id for e in entries):
        entries.append(new_entry)
        try:
            with open(rejected_file, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
            logger.info(f"Successfully logged rejected post {post_id} to database.")
        except Exception as e:
            logger.error(f"Failed to write to rejected_posts.json: {e}")
