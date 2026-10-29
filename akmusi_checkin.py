"""Akmusi AI daily check-in entry point using the shared New API client."""

import orbitix_checkin


def main():
    return orbitix_checkin.main(profile='akmusi')


if __name__ == '__main__':
    raise SystemExit(main())
