import logging
from time import time

logger = logging.getLogger(__name__)
pattern_list = [
    "( ●    )",
    "(  ●   )",
    "(   ●  )",
    "(    ● )",
    "(     ●)",
    "(    ● )",
    "(   ●  )",
    "(  ●   )",
    "( ●    )",
    "(●     )"
]


def string_to_bool(input):
    return input.lower() not in ['0', 'false', 'f']


def wait(seconds, exit_event, interval=1, extra_message='...', log_signal=True):
    """

    :type seconds: float
    :type exit_event: threading.Event

    """
    start_time = time()
    end_time = start_time + seconds
    digits = str(len(str(seconds)) + 1)
    format_str = '{}{:' + digits + '.0f}{}'
    pattern_num = 0
    while time() < end_time and not exit_event.is_set():
        if log_signal:
            logger.info(format_str.format(
                pattern_list[pattern_num % len(pattern_list)],
                end_time - time(),
                extra_message
            ))
        pattern_num += 1
        exit_event.wait(interval - (time() - start_time) % interval)
