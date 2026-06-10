#!/usr/bin/env python3
"""FIDE Rating Calculator — CLI formatting library."""

import json
import sys
import os
from urllib.request import Request, urlopen
from urllib.parse import urljoin

CLR_RESET = "\033[0m"
CLR_RED = "\033[0;31m"
CLR_GREEN = "\033[0;32m"
CLR_YELLOW = "\033[1;33m"
CLR_CYAN = "\033[0;36m"
CLR_BOLD = "\033[1m"
CLR_DIM = "\033[2m"


def api_call(host: str, endpoint: str, payload: dict) -> dict | None:
    url = urljoin(host.rstrip("/") + "/", f"api/{endpoint.lstrip('/')}")
    req = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"{CLR_RED}[ERROR]{CLR_RESET} API error: {e}", file=sys.stderr)
        return None


def cmd_estimate(data: dict, username: str, platform: str, host: str):
    print(f"\n{CLR_CYAN}=== FIDE Rating Estimate ==={CLR_RESET}\n")
    print(f"  {'\u0418\u0433\u0440\u043e\u043a:':12} {username}")
    print(f"  {'\u041f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430:':12} {platform}")
    print(f"  {'API:':12} {host}/api/\n")

    if "error" in data:
        print(f"{CLR_RED}[ERROR]{CLR_RESET} {data['error']}")
        sys.exit(1)

    tc = data.get("results", data.get("time_controls", []))
    print(f"  {'\u0412\u0440\u0435\u043c\u0435\u043d\u043d\u043e\u0439 \u043a\u043e\u043d\u0442\u0440\u043e\u043b\u044c':20} {'\u0420\u0435\u0439\u0442\u0438\u043d\u0433':>8} {'FIDE':>8} {'\u0422\u043e\u0447\u043d.':>6}")
    print(f"  {'\u2500' * 44}")
    for t in tc:
        name = t.get("time_class", "?")
        plat = t.get("user_platform_rating", "\u2014")
        fide = t.get("estimated_fide", "\u2014")
        acc = t.get("accuracy", "\u2014")
        print(f"  {name:20} {str(plat):>8} {str(fide):>8} {str(acc):>6}")

    print()
    if "final_estimate" in data:
        print(f"  {'\u0418\u0442\u043e\u0433\u043e\u0432\u0430\u044f \u043e\u0446\u0435\u043d\u043a\u0430:':20} {data['final_estimate']}")
    if "confidence" in data:
        print(f"  {'\u0423\u0432\u0435\u0440\u0435\u043d\u043d\u043e\u0441\u0442\u044c:':20} {data['confidence']}")
    if "num_anchors" in data:
        print(f"  {'\u042f\u043a\u043e\u0440\u0435\u0439:':20} {data['num_anchors']}")


def cmd_rating(data: dict):
    if "error" in data:
        print("N/A")
        sys.exit(1)
    print(data.get("final_estimate", data.get("estimated_fide", "N/A")))


def cmd_anchors(data: dict):
    print(f"\n{CLR_CYAN}=== Anchors (\u0442\u0438\u0442\u0443\u043b\u043e\u0432\u0430\u043d\u043d\u044b\u0435 \u0441\u043e\u043f\u0435\u0440\u043d\u0438\u043a\u0438) ==={CLR_RESET}\n")
    if "error" in data:
        print(f"{CLR_RED}[ERROR]{CLR_RESET} {data['error']}")
        sys.exit(1)

    anchors = data.get("anchors", [])
    if not anchors:
        print("  \u041d\u0435\u0442 \u044f\u043a\u043e\u0440\u0435\u0439 \u0434\u043b\u044f \u044d\u0442\u043e\u0433\u043e \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f.")
        return

    print(f"  {'\u0421\u043e\u043f\u0435\u0440\u043d\u0438\u043a':25} {'\u0422\u0438\u0442\u0443\u043b':>6} {'\u041f\u043b\u0430\u0442\u0444.':>10} {'FIDE':>6} {'Offset':>7} {'\u0412\u0435\u0441':>6}")
    print(f"  {'\u2500' * 62}")
    for a in anchors:
        opp = a.get("opponent", "?")[:24]
        title = a.get("title", "")
        pr = a.get("platform_rating", 0)
        fide = a.get("fide_rating", 0)
        off = a.get("adjusted_offset", 0)
        w = a.get("weight", 0)
        print(f"  {opp:25} {str(title):>6} {str(pr):>10} {str(fide):>6} {str(off):>7.1f} {str(w):>6}")

    print(f"\n  \u0412\u0441\u0435\u0433\u043e \u044f\u043a\u043e\u0440\u0435\u0439: {len(anchors)}")


def cmd_daily(data: dict):
    if "error" in data:
        print(f"{CLR_RED}[ERROR]{CLR_RESET} {data['error']}")
        sys.exit(1)

    daily = data.get("daily_estimates", [])
    if not daily:
        print("  \u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u0434\u043b\u044f \u0434\u043d\u0435\u0432\u043d\u043e\u0439 \u0434\u0438\u043d\u0430\u043c\u0438\u043a\u0438.")
        return

    print(f"\n{CLR_CYAN}=== \u0414\u043d\u0435\u0432\u043d\u0430\u044f \u0434\u0438\u043d\u0430\u043c\u0438\u043a\u0430 ==={CLR_RESET}\n")
    print(f"  {'\u0414\u0430\u0442\u0430':15} {'\u0420\u0435\u0439\u0442\u0438\u043d\u0433':>8} {'FIDE':>8} {'\u042f\u043a\u043e\u0440\u0435\u0439':>8} {'\u0421\u043c\u0435\u0449.':>7}")
    print(f"  {'\u2500' * 48}")
    for d in daily:
        date = d.get("date", "")[:10]
        ur = d.get("user_platform_rating", "\u2014")
        fide = d.get("estimated_fide", "\u2014")
        na = d.get("num_anchors", 0)
        off = d.get("avg_offset", 0)
        print(f"  {date:15} {str(ur):>8} {str(fide):>8} {str(na):>8} {str(off):>7}")


def cmd_about():
    print(f"""
{CLR_BOLD}Ameni FIDE Rating Calculator{CLR_RESET}
CLI-\u0430\u0433\u0435\u043d\u0442 \u0434\u043b\u044f \u043e\u0446\u0435\u043d\u043a\u0438 FIDE-\u0440\u0435\u0439\u0442\u0438\u043d\u0433\u0430
\u043f\u043e \u043f\u0430\u0440\u0442\u0438\u044f\u043c \u0441 Lichess \u0438 Chess.com

https://github.com/inzexg-coder/fide-rating-calc

\u041e\u0441\u043d\u043e\u0432\u0430\u043d \u043d\u0430 Anchor-\u043c\u0435\u0442\u043e\u0434\u0435:
\u043f\u043e\u0438\u0441\u043a \u0442\u0438\u0442\u0443\u043b\u043e\u0432\u0430\u043d\u043d\u044b\u0445 \u0441\u043e\u043f\u0435\u0440\u043d\u0438\u043a\u043e\u0432
\u0441 \u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u043c FIDE-\u0440\u0435\u0439\u0442\u0438\u043d\u0433\u043e\u043c.

\u041a\u043e\u043c\u0430\u043d\u0434\u044b:
  estimate <user>   \u041e\u0446\u0435\u043d\u043a\u0430 FIDE-\u0440\u0435\u0439\u0442\u0438\u043d\u0433\u0430
  rating <user>     \u0422\u043e\u043b\u044c\u043a\u043e \u0438\u0442\u043e\u0433\u043e\u0432\u044b\u0439 \u0440\u0435\u0439\u0442\u0438\u043d\u0433
  anchors <user>    \u0421\u043f\u0438\u0441\u043e\u043a \u044f\u043a\u043e\u0440\u0435\u0439
  daily <user>      \u0414\u043d\u0435\u0432\u043d\u0430\u044f \u0434\u0438\u043d\u0430\u043c\u0438\u043a\u0430
  check <user>      \u041f\u043e\u043b\u043d\u0430\u044f \u0438\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f
  about             \u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f
  help              \u041f\u043e\u043b\u043d\u044b\u0439 \u043c\u0430\u043d\u0443\u0430\u043b

\u041e\u043f\u0446\u0438\u0438:
  --platform, -p  \u041f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430 (lichess/chesscom)
  --chesscom      \u0421\u043e\u043a\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u0434\u043b\u044f --platform chesscom
  --host URL      API-\u0445\u043e\u0441\u0442 (\u043f\u043e \u0443\u043c\u043e\u043b\u0447. https://amenoke.ru)
""")


def main():
    args = sys.argv[1:]
    if not args:
        cmd_about()
        return

    host = "https://amenoke.ru"
    platform = "lichess"
    command = ""
    username = ""

    # Filter global options
    filtered = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
            continue
        elif a in ("--platform", "-p") and i + 1 < len(args):
            platform = args[i + 1]
            i += 2
            continue
        elif a == "--chesscom":
            platform = "chesscom"
            i += 1
            continue
        elif a == "--lichess":
            platform = "lichess"
            i += 1
            continue
        elif a.startswith("--"):
            i += 1
            continue
        filtered.append(a)
        i += 1

    if filtered:
        command = filtered[0]
    if len(filtered) > 1:
        username = filtered[1]

    if command in ("help", "-h", "--help") or not command:
        cmd_about()
        return

    payload = {"platform": platform, "username": username}

    if command in ("estimate", "anchors", "daily", "check", "rating"):
        if not username:
            print(f"{CLR_RED}[ERROR]{CLR_RESET} \u0418\u043c\u044f \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e")
            sys.exit(1)

        data = api_call(host, "estimate", payload)
        if data is None:
            sys.exit(1)

        if command == "estimate":
            cmd_estimate(data, username, platform, host)
        elif command == "rating":
            cmd_rating(data)
        elif command == "anchors":
            cmd_anchors(data)
        elif command == "daily":
            cmd_daily(data)
        elif command == "check":
            cmd_estimate(data, username, platform, host)
            print()
            cmd_anchors(data)

    elif command == "about":
        cmd_about()
    else:
        print(f"{CLR_RED}[ERROR]{CLR_RESET} \u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043a\u043e\u043c\u0430\u043d\u0434\u0430: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
