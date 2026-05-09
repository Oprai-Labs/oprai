def sanitize_user_input_for_prompt(text: str, max_length: int = 4000) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) > max_length:
        text = text[:max_length] + "...[truncated]"
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text
