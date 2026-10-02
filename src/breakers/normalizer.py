import re

HEX_ADDR_REGEX = re.compile(r"0x[0-9a-fA-F]+")
TIMESTAMP_REGEX = re.compile(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
TIME_ONLY_REGEX = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b")

# Matches File ".../filename.ext" or File "C:\...\filename.ext"
FILE_PATH_IN_QUOTES = re.compile(r'File\s+["\'](?:[A-Za-z]:\\[^"\']*|[/\\][^"\']*)?[/\\]([^/\\]+\.[a-zA-Z0-9_]+)["\']')
# Generic absolute Windows or POSIX paths in text
GENERIC_ABS_PATH = re.compile(r'(?:[A-Za-z]:\\[^ \t\n\r"\'<>]+|[/\\](?:[a-zA-Z0-9_\.-]+[/\\])+[a-zA-Z0-9_\.-]+\.[a-zA-Z0-9]+)')


def normalize_traceback(traceback_text: str) -> str:
    text = traceback_text.strip()
    
    # 1. Normalize memory addresses
    text = HEX_ADDR_REGEX.sub("<hex_addr>", text)
    
    # 2. Normalize timestamps
    text = TIMESTAMP_REGEX.sub("<timestamp>", text)
    text = TIME_ONLY_REGEX.sub("<timestamp>", text)
    
    # 3. Normalize File "..." paths in tracebacks to File "<path>/basename"
    def _file_path_replacer(m: re.Match) -> str:
        basename = m.group(1)
        if re.match(r"^tmp[a-zA-Z0-9_\.-]*\.[a-zA-Z0-9_]+$", basename, re.IGNORECASE):
            basename = "<tmp_file>"
        return f'File "<path>/{basename}"'
        
    text = FILE_PATH_IN_QUOTES.sub(_file_path_replacer, text)
    
    # 4. Normalize any remaining generic absolute paths
    def _generic_path_replacer(m: re.Match) -> str:
        full = m.group(0)
        parts = re.split(r"[/\\]", full)
        basename = parts[-1] if parts else ""
        if re.match(r"^tmp[a-zA-Z0-9_\.-]*\.[a-zA-Z0-9_]+$", basename, re.IGNORECASE):
            basename = "<tmp_file>"
        return f"<path>/{basename}" if basename else "<path>"
        
    text = GENERIC_ABS_PATH.sub(_generic_path_replacer, text)
    
    # 5. Remove multiple consecutive whitespace
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()
