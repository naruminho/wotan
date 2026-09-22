import sys


def main(argv):
    if argv and argv[0] == "count":
        print(len(argv) - 1)
        return 0
    print("usage: tool.py {count} ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
