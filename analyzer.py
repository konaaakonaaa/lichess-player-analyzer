"""
Lichess Player Analyzer
Стримит партии игрока из API Lichess и выводит статистику в терминале.

Запуск:
    python analyzer.py DrNykterstein
    python analyzer.py DrNykterstein -n 500 --perf blitz
Выход: Ctrl+C
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

import requests
from rich.console import Console
from rich.table import Table

console = Console()
API = "https://lichess.org/api/games/user/{}"


def stream_games(username, n, perf=None, token=None):
    """Генератор: отдаёт партии по одной, ничего не сохраняя на диск."""
    params = {"max": n, "opening": "true", "moves": "true", "clocks": "false"}
    if perf:
        params["perfType"] = perf
    headers = {"Accept": "application/x-ndjson"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    while True:
        r = requests.get(API.format(username), params=params,
                         headers=headers, stream=True, timeout=30)
        if r.status_code == 429:
            console.print("[yellow]Лимит API, жду 60 сек...[/]")
            time.sleep(60)
            continue
        if r.status_code == 404:
            console.print(f"[red]Игрок '{username}' не найден[/]")
            sys.exit(1)
        r.raise_for_status()
        break

    for line in r.iter_lines():
        if line:
            yield json.loads(line)


def analyze(username, games):
    low = username.lower()
    s = {
        "total": 0,
        "by_color": {"white": Counter(), "black": Counter()},
        "by_speed": defaultdict(Counter),
        "openings": {"white": defaultdict(Counter), "black": defaultdict(Counter)},
        "ending": Counter(),
        "lengths": [],
        "hours": defaultdict(Counter),
        "ratings": defaultdict(list),
    }

    for g in games:
        players = g.get("players", {})
        color = None
        for c in ("white", "black"):
            u = players.get(c, {}).get("user", {})
            if u.get("name", "").lower() == low:
                color = c
        if color is None:
            continue

        winner = g.get("winner")
        res = "draw" if winner is None else ("win" if winner == color else "loss")
        speed = g.get("speed", "?")

        s["total"] += 1
        s["by_color"][color][res] += 1
        s["by_speed"][speed][res] += 1

        opening = g.get("opening", {}).get("name", "Без дебюта").split(":")[0]
        s["openings"][color][opening][res] += 1

        s["ending"][(res, g.get("status", "?"))] += 1
        s["lengths"].append(len(g.get("moves", "").split()) // 2 + 1)

        hour = datetime.fromtimestamp(g["createdAt"] / 1000).hour
        part = ("ночь" if hour < 6 else "утро" if hour < 12
                else "день" if hour < 18 else "вечер")
        s["hours"][part][res] += 1

        rating = players[color].get("rating")
        if rating:
            s["ratings"][speed].append((g["createdAt"], rating))
    return s


def pct(c, key):
    t = sum(c.values())
    return f"{100 * c[key] / t:.0f}%" if t else "-"


def wld(c):
    return f"{c['win']}/{c['draw']}/{c['loss']}"


def sparkline(values, width=40):
    bars = "▁▂▃▄▅▆▇█"
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    if hi == lo:
        return bars[0] * len(values)
    return "".join(bars[int((v - lo) / (hi - lo) * 7)] for v in values)


def show(username, s):
    if not s["total"]:
        console.print("[red]Нет партий для анализа[/]")
        return

    console.rule(f"[bold]Анализ {username}: {s['total']} партий")

    t = Table(title="Результаты по цвету (победы/ничьи/поражения)")
    t.add_column("Цвет"); t.add_column("П/Н/П"); t.add_column("Винрейт")
    for c, name in (("white", "Белые"), ("black", "Чёрные")):
        t.add_row(name, wld(s["by_color"][c]), pct(s["by_color"][c], "win"))
    console.print(t)

    t = Table(title="По контролю времени")
    t.add_column("Режим"); t.add_column("Партий"); t.add_column("П/Н/П"); t.add_column("Винрейт")
    for sp, c in sorted(s["by_speed"].items(), key=lambda x: -sum(x[1].values())):
        t.add_row(sp, str(sum(c.values())), wld(c), pct(c, "win"))
    console.print(t)

    for color, name in (("white", "белыми"), ("black", "чёрными")):
        t = Table(title=f"Топ-7 дебютов {name}")
        t.add_column("Дебют"); t.add_column("Партий"); t.add_column("П/Н/П"); t.add_column("Винрейт")
        ops = sorted(s["openings"][color].items(), key=lambda x: -sum(x[1].values()))[:7]
        for op, c in ops:
            t.add_row(op, str(sum(c.values())), wld(c), pct(c, "win"))
        console.print(t)

    rated = []
    for color in ("white", "black"):
        for op, c in s["openings"][color].items():
            if sum(c.values()) >= 5:
                rated.append((c["win"] / sum(c.values()), op, color))
    if rated:
        rated.sort()
        console.print("\n[red]Слабые места:[/]")
        for wr, op, col in rated[:3]:
            console.print(f"  {op} ({col}): {wr:.0%}")
        console.print("[green]Сильные стороны:[/]")
        for wr, op, col in rated[-3:][::-1]:
            console.print(f"  {op} ({col}): {wr:.0%}")

    t = Table(title="Как заканчиваются партии")
    t.add_column("Итог"); t.add_column("Причина"); t.add_column("Кол-во")
    names = {"win": "Победа", "loss": "Поражение", "draw": "Ничья"}
    for (res, status), cnt in s["ending"].most_common(8):
        t.add_row(names[res], status, str(cnt))
    console.print(t)

    t = Table(title="Время суток")
    t.add_column("Когда"); t.add_column("Партий"); t.add_column("Винрейт")
    for part in ("ночь", "утро", "день", "вечер"):
        c = s["hours"][part]
        if c:
            t.add_row(part, str(sum(c.values())), pct(c, "win"))
    console.print(t)

    L = s["lengths"]
    console.print(f"\nСредняя длина партии: [bold]{sum(L) / len(L):.0f}[/] ходов "
                  f"(коротких до 20 ходов: {sum(1 for x in L if x < 20) * 100 // len(L)}%)")

    for sp, data in s["ratings"].items():
        if len(data) >= 10:
            vals = [r for _, r in sorted(data)]
            console.print(f"\nРейтинг {sp}: {vals[0]} → {vals[-1]} "
                          f"(макс {max(vals)})\n{sparkline(vals)}")


def main():
    p = argparse.ArgumentParser(description="Анализ партий игрока Lichess")
    p.add_argument("username", nargs="?")
    p.add_argument("-n", type=int, default=500, help="сколько партий (по умолчанию 500)")
    p.add_argument("--perf", help="bullet/blitz/rapid/classical")
    p.add_argument("--token", help="токен Lichess (необязательно, ускоряет загрузку)")
    a = p.parse_args()

    username = a.username or console.input("Ник на Lichess: ").strip()
    try:
        with console.status(f"Загружаю партии {username}..."):
            s = analyze(username, stream_games(username, a.n, a.perf, a.token))
        show(username, s)
    except KeyboardInterrupt:
        console.print("\nВыход.")


if __name__ == "__main__":
    main()
