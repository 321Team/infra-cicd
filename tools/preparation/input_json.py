"""Bounded local JSON input; do not echo input values or exception details."""
import json
import os
import stat


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('NONFINITE_JSON_NUMBER')


def read_record(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('INPUT_NOT_REGULAR')
        data = stream.read(1048577)
    if len(data) > 1048576:
        raise ValueError('INPUT_TOO_LARGE')
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)
