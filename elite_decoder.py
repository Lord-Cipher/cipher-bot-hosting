
import base64
import zlib
import gzip
import re
import io
import marshal
import types
from typing import List, Optional, Tuple, Any

class EliteDecoder:
    """
    Advanced multi-level decoding engine for Python scripts.
    Handles Phobos-style layered encoding, compression, and bytecode extraction.
    """
    
    def __init__(self, max_depth: int = 50):
        self.max_depth = max_depth
        self.seen_payloads = set()

    def decode(self, content: str) -> Tuple[str, List[str]]:
        """
        Main entry point. Returns (decoded_content, list_of_methods_found).
        """
        current = content
        methods = []
        depth = 0
        
        while depth < self.max_depth:
            # 1. Check for Base64 layers
            b64_decoded = self._try_base64(current)
            if b64_decoded:
                current = b64_decoded
                methods.append("Base64")
                depth += 1
                continue
                
            # 2. Check for Zlib/Gzip layers
            comp_decoded = self._try_decompression(current)
            if comp_decoded:
                current = comp_decoded
                methods.append("Decompression (Zlib/Gzip)")
                depth += 1
                continue
                
            # 3. Check for Hex layers
            hex_decoded = self._try_hex(current)
            if hex_decoded:
                current = hex_decoded
                methods.append("Hex")
                depth += 1
                continue

            # 4. Check for Marshal Bytecode
            marshal_decoded = self._try_marshal(current)
            if marshal_decoded:
                current = marshal_decoded
                methods.append("Marshal Bytecode Extraction")
                depth += 1
                continue
            
            # No more layers found
            break
            
        return current, list(dict.fromkeys(methods))

    def _try_base64(self, s: str) -> Optional[str]:
        # Look for base64.b64decode("...") or just long b64 strings
        patterns = [
            r'base64\.b64decode\s*\(\s*["\']([A-Za-z0-9+/=]{20,})["\']\s*\)',
            r'b64decode\s*\(\s*["\']([A-Za-z0-9+/=]{20,})["\']\s*\)',
            r'["\']([A-Za-z0-9+/=]{80,})["\']', # Naked long strings
            r'eval\s*\(\s*base64\.b64decode\s*\(\s*b?["\']([A-Za-z0-9+/=]{20,})["\']\s*\)\.decode\(\)\s*\)'
        ]
        for p in patterns:
            matches = re.findall(p, s)
            for m in matches:
                try:
                    raw = base64.b64decode(m)
                    # Try plain text
                    try:
                        decoded = raw.decode('utf-8')
                        if any(x in decoded for x in ["import", "exec", "eval", "os", "sys", "requests"]):
                            return decoded
                    except Exception: pass
                    
                    # Try Zlib inside Base64 (Very common in Phobos)
                    try:
                        decoded = zlib.decompress(raw).decode('utf-8', errors='ignore')
                        return decoded
                    except Exception: pass
                    
                except Exception: continue
        return None

    def _try_decompression(self, s: str) -> Optional[str]:
        # Look for zlib.decompress or gzip.decompress
        # Also look for raw zlib headers \x78\x9c
        if "zlib" in s or "decompress" in s or "\\x78\\x9c" in s:
            # Extract potential binary blobs
            blobs = re.findall(r'b?["\']((?:\\x[0-9a-fA-F]{2})+)["\']', s)
            for b in blobs:
                try:
                    raw = bytes.fromhex(b.replace("\\x", ""))
                    # Try Zlib
                    try: 
                        res = zlib.decompress(raw).decode('utf-8', errors='ignore')
                        if len(res) > 10: return res
                    except Exception: pass
                    # Try Gzip
                    try: 
                        res = gzip.decompress(raw).decode('utf-8', errors='ignore')
                        if len(res) > 10: return res
                    except Exception: pass
                except Exception: continue
        return None

    def _try_hex(self, s: str) -> Optional[str]:
        # Look for long hex strings \x41\x42...
        hex_pattern = r'((?:\\x[0-9a-fA-F]{2}){20,})'
        matches = re.findall(hex_pattern, s)
        for m in matches:
            try:
                h = m.replace("\\x", "")
                decoded = bytes.fromhex(h).decode('utf-8', errors='ignore')
                if len(decoded) > 10: return decoded
            except Exception: continue
        return None

    def _try_marshal(self, s: str) -> Optional[str]:
        # Look for marshal.loads(b"...")
        if "marshal" in s and "loads" in s:
            blobs = re.findall(r'marshal\.loads\s*\(\s*b["\']((?:\\x[0-9a-fA-F]{2})+)["\']\s*\)', s)
            for b in blobs:
                try:
                    raw = bytes.fromhex(b.replace("\\x", ""))
                    code_obj = marshal.loads(raw)
                    if isinstance(code_obj, types.CodeType):
                        # We can't "decompile" easily without external tools like uncompyle6,
                        # but we can extract strings and constants which usually reveal the logic.
                        strings = [str(c) for c in code_obj.co_consts if isinstance(c, (str, bytes))]
                        return "\n".join(strings)
                except Exception: continue
        return None

    def detect_phobos(self, s: str) -> bool:
        """Detects signatures of the Phobos/Phobo obfuscator."""
        phobos_sigs = [
            r'__PHOBOS__', r'phobo_version', 
            r'getattr\(.*[\'"]\x73\x79\x73\x74\x65\x6d[\'"]\)', # system
            r'eval\(compile\(.*[\'"]<string>[\'"]'
        ]
        return any(re.search(sig, s) for sig in phobos_sigs)

if __name__ == "__main__":
    # Test with a simple layered payload
    test_code = 'import base64; exec(base64.b64decode("aW1wb3J0IG9zOyBvcy5zeXN0ZW0oImVjaG8gaGFja2VkIik="))'
    decoder = EliteDecoder()
    decoded, methods = decoder.decode(test_code)
    print(f"Methods: {methods}")
    print(f"Decoded: {decoded}")
