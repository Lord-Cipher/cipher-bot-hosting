import sys
import os
import requests
import json

# Mock the environment and settings
os.environ["OPENROUTER_KEY"] = "your-key-here"
os.environ["POLLINATIONS_KEY"] = "your-key-here"

def get_setting(key, default): return True # Mock all enabled

def sc(text): return text # Mock small caps

def _call_ai_api(prompt: str, user_plan: str = "free") -> str:
    print(f"\n[TEST] Prompt: '{prompt}' | Plan: {user_plan}")
    
    providers = []
    if user_plan in ["enterprise", "lifetime"]:
        providers = ["openrouter", "pollinations", "duckduckgo"]
    elif user_plan == "pro":
        providers = ["pollinations", "duckduckgo"]
    else:
        providers = ["duckduckgo", "pollinations"]

    for provider in providers:
        print(f" -> Trying {provider}...")
        
        if provider == "openrouter":
            or_key = os.environ.get("OPENROUTER_KEY")
            try:
                is_coding = any(k in prompt.lower() for k in ["python", "code", "fix", "error", "script"])
                or_model = "google/gemma-4-26b-a4b-it:free" # Use a known working free model
                headers = {"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"}
                payload = {"model": or_model, "messages": [{"role": "user", "content": prompt}]}
                r = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=15)
                if r.status_code == 200:
                    res = r.json()
                    content = res["choices"][0]["message"]["content"]
                    print(f" ✅ OpenRouter Success: {content[:50]}...")
                    return content
                else:
                    print(f" ❌ OpenRouter Failed: {r.status_code} {r.text}")
            except Exception as e: print(f" ❌ OpenRouter Error: {e}")

        if provider == "pollinations":
            pol_key = os.environ.get("POLLINATIONS_KEY")
            try:
                headers = {"Authorization": f"Bearer {pol_key}", "Content-Type": "application/json"}
                payload = {"model": "openai", "messages": [{"role": "user", "content": prompt}]}
                r = requests.post("https://gen.pollinations.ai/v1/chat/completions", json=payload, headers=headers, timeout=15)
                if r.status_code == 200:
                    res = r.json()
                    content = res["choices"][0]["message"]["content"]
                    print(f" ✅ Pollinations Success: {content[:50]}...")
                    return content
                else:
                    print(f" ❌ Pollinations Failed: {r.status_code} {r.text}")
            except Exception as e: print(f" ❌ Pollinations Error: {e}")

        if provider == "duckduckgo":
            try:
                headers = {"x-vqd-accept": "1"}
                s = requests.get("https://duckduckgo.com/aichat/v1/status", headers=headers, timeout=5)
                vqd = s.headers.get("x-vqd-token")
                if vqd:
                    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
                    headers["x-vqd-token"] = vqd
                    headers["Content-Type"] = "application/json"
                    r = requests.post("https://duckduckgo.com/aichat/v1/chat", json=payload, headers=headers, timeout=10)
                    if r.status_code == 200:
                        full_res = ""
                        for line in r.iter_lines():
                            if line:
                                line_str = line.decode('utf-8')
                                if line_str.startswith('data: '):
                                    try:
                                        data = json.loads(line_str[6:])
                                        if data.get('message'): full_res += data['message']
                                        if data.get('action') == 'end': break
                                    except: pass
                        if full_res:
                            print(f" ✅ DuckDuckGo Success: {full_res[:50]}...")
                            return full_res
                print(f" ❌ DuckDuckGo Failed: No VQD or response")
            except Exception as e: print(f" ❌ DuckDuckGo Error: {e}")

    return "FALLBACK: Hello, Commander!"

if __name__ == "__main__":
    # Test 1: Enterprise (OpenRouter)
    _call_ai_api("Write a hello world in python", "enterprise")
    
    # Test 2: Pro (Pollinations)
    _call_ai_api("How to fix a syntax error?", "pro")
    
    # Test 3: Free (DuckDuckGo)
    _call_ai_api("Hello", "free")
