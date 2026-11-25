"""Compares two CSV files line by line and describes every difference."""


def read_lines(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read().replace("\r\n", "\n").split("\n")


def compare_files(expected_path, actual_path, limit=10):
    """Returns (number_of_differences, list_of_example_messages)."""
    expected, actual = read_lines(expected_path), read_lines(actual_path)
    messages, count = [], 0
    for i in range(max(len(expected), len(actual))):
        e = expected[i] if i < len(expected) else "<missing>"
        a = actual[i] if i < len(actual) else "<missing>"
        if e != a:
            count += 1
            if len(messages) < limit:
                messages.append("line %d: expected [%s] got [%s]" % (i + 1, e, a))
    return count, messages


def compare_folders(expected_dir, actual_dir):
    """Compare summary.csv and rejects.csv. Returns {file: (diff_count, examples)}."""
    return {name: compare_files(expected_dir / name, actual_dir / name) for name in ("summary.csv", "rejects.csv")}
