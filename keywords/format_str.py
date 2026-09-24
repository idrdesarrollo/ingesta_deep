def format_str(text: str):
    text = text.replace("\n", "\\\\n").replace("\t", "\\\\t").replace('\xa0', ' ').replace()