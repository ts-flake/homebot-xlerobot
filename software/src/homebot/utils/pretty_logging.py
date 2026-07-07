import logging
import unicodedata
from datetime import datetime

_ansi_styles = {
    'bold': 1,
    'italic': 3,
    'underline': 4,
}


_ansi_colors = {
    'black': 0,
    'red': 1,
    'green': 2,
    'yellow': 3,
    'blue': 4,
    'magenta': 5,
    'purple': 5,
    'cyan': 6,
    'white': 7
}


def apply_ansi_style(text: str, *, color: str = None, styles: str = None, background: str = None) -> str:
    """
    Returns an ANSI-styled text string.

    Args:
        text: input text
        color: single text color, e.g., 'purple', 'light purple'
        styles: single or multiple text styles separated by space, e.g. 'bold', 'bold italic'
        background: single background color
    """
    format_list = []
    if styles is not None:
        styles = set(styles.lower().split(' '))
        codes = [_ansi_styles.get(s, -1) for s in styles]
        codes = [str(code) for code in codes if code > 0]
        format_list.extend(codes)
    if color is not None:
        color = set(color.lower().split(' '))
        light = 60 if 'light' in color else 0
        code = [_ansi_colors.get(c, -1) for c in color][0]
        format_list.append(str(code + 30 + light)) # text color starts at 30
    if background is not None:
        background = set(background.lower().split(' '))
        light = 60 if 'light' in background else 0
        code = [_ansi_colors.get(b, -1) for b in background][0]
        format_list.append(str(code + 40 + light)) # background color starts at 40
    return f'\033[{";".join(format_list)}m{text}\033[0m'


def _text_width(text: str) -> int:
    """Visual width of `text` in terminal columns.

    Counts East Asian wide/fullwidth characters (most emojis) as 2 columns
    and ignores combining marks and zero-width (U+200d) joiners.
    """
    width = 0
    for char in text:
        if char == '‍' or unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
    return width


def make_callout_text(title: str, *, content: str = None, icon: str = None) -> str:
    icon = icon or ''
    icon_title = icon + ' ' + title
    max_len = _text_width(icon_title)

    title_only = True
    if content:
        title_only = False
        lines = content.split('\n')
        max_len = max(
            max(_text_width(line) for line in lines),
            max_len
        )

    border = '╭' + '─' * (max_len + 2) + '╮'
    bottom = '╰' + '─' * (max_len + 2) + '╯'

    body_list = (
        [] if title_only
        else (
            ['│ ' + '─' * max_len + ' │']
            + ['│ ' + line + ' ' * (max_len - _text_width(line)) + ' │' for line in lines]
        )
    )

    return '\n'.join(
        [border]
        + ['│ ' + icon_title + ' ' * (max_len - _text_width(icon_title)) + ' │']
        + body_list
        + [bottom]
    )


def _init_logging(
        name: str | None = None,
        level: str = "INFO"
):
    """
    Initialize logging configuration with colored output.

    Args:
        console_level: Logging level for console output

    Returns:
        logging.Logger: The logger instance
    """
    def custom_format(record: logging.LogRecord) -> str:
        LEVEL_COLOR_MAP: dict[str, str] = {
            'DEBUG': 'light white',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red',
        }
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fnameline = f"{record.pathname}:{record.lineno}"
        def color_text(text):
            return apply_ansi_style(
                text,
                styles='bold' if record.levelname == 'CRITICAL' else None,
                color=LEVEL_COLOR_MAP.get(record.levelname, None),
                background=None
            )
        return (
        color_text(f"[{dt}] [{record.levelname:<8}] ")
        + f"\033[90m[...{fnameline[-15:]:>15}]\033[0m "
        + record.getMessage()
        )

    logger = logging.getLogger(name=name)
    logger.setLevel(level.upper())

    # Clear any existing handlers
    logger.handlers.clear()

    # Custom formatter
    formatter = logging.Formatter()
    formatter.format = custom_format

    # Console logging
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level.upper())
    logger.addHandler(console_handler)


def get_logger(name: str | None = None, level: str | None = None):
    import os
    # determine log level from config if available
    level = level or os.environ.get("HOMEBOT_LOG_LEVEL")
    if level is None:
        try:
            from configs.config import Config
            level = Config().logging.level
        except Exception:
            level = "DEBUG"
    
    logger = logging.getLogger(name=name)
    _init_logging(name, level)
    return logger

if __name__ == "__main__":
    logger = get_logger(__name__, level="info")

    print(f"logger name: {logger.name}")
    print(f"logger level: {logger.getEffectiveLevel()}")
    logger.debug("Hello, world!")
    logger.info("Hello, world!")
    logger.warning("Hello, world!")
    logger.error("Hello, world!")
    logger.critical(apply_ansi_style("Hello, world!", color='purple'))

    callout = make_callout_text(
        '测试 callout',
        content='single line\nmultiplines\nand something verrrrrrrrrrrrrry long\n中文内容',
        icon='✅'
    )
    color_callout = apply_ansi_style(callout, color='green', styles='bold')
    logger.info('\n' + color_callout)

    callout = make_callout_text(
        '测试 callout title only',
        icon='🆗'
    )
    color_callout = apply_ansi_style(callout, color='blue')
    logger.info('\n' + color_callout)
