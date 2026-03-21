import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, unquote

import psycopg2
import discord
from discord.ext import commands, tasks

# 설정
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

KST = ZoneInfo("Asia/Seoul")
TEXT_CHANNEL_NAME = "채팅"

TRACK_CHANNELS = {
    "📖 열공": "study",
    "☘️ 휴식": "rest"
}

TARGET_USERS = ["멜마", "배키"]

STUDY_CHANNEL_NAME = "📖 열공"
REST_CHANNEL_NAME = "☘️ 휴식"

# 디스코드 설정
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 런타임 상태
active_sessions = {}

last_log = None
last_deleted_log = None
last_reset_backup = None
last_night_message = None

study_alerts = {
    "멜마": set(),
    "배키": set()
}

rest_tasks = {}
study_tasks = {}

# 공통 유틸
def now():
    return datetime.now(KST)


def format_time(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60

    if h == 0 and m == 0:
        return f"{s:02d}초"
    if h == 0:
        return f"{m:02d}분 {s:02d}초"
    return f"{h}시간 {m:02d}분 {s:02d}초"


def get_text_channel(guild):
    for ch in guild.text_channels:
        if ch.name == TEXT_CHANNEL_NAME:
            return ch
    return guild.system_channel or guild.text_channels[0]


def get_member_by_display_name(guild, user_name):
    return discord.utils.get(guild.members, display_name=user_name)


def cancel_task(task_dict, user_id):
    task = task_dict.pop(user_id, None)
    if task:
        task.cancel()

# DB
def get_db_connection():
    raw_url = DATABASE_URL
    url = urlparse(raw_url)

    password = unquote(url.password) if url.password else None

    return psycopg2.connect(
        dbname=url.path[1:],
        user=url.username,
        password=password,
        host=url.hostname,
        port=int(url.port) if url.port else 5432,
        sslmode="require"
    )


def db_execute(query, params=(), fetch=False):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(query, params)
        result = cur.fetchall() if fetch else None
        conn.commit()
        return result
    finally:
        cur.close()
        conn.close()


def init_db():
    db_execute("""
        CREATE TABLE IF NOT EXISTS study_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            user_name TEXT,
            channel TEXT,
            duration INTEGER,
            date TEXT
        )
    """)

    db_execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            user_id BIGINT PRIMARY KEY,
            user_name TEXT,
            channel TEXT,
            start TIMESTAMP
        )
    """)

# 시간 / 기록 관련
def save_log(user_id, user_name, channel, seconds, date):
    db_execute("""
        INSERT INTO study_logs (user_id, user_name, channel, duration, date)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, user_name, channel, seconds, date))


def get_time(user_id, channel, today_only=False):
    query = """
        SELECT SUM(duration)
        FROM study_logs
        WHERE user_id=%s AND channel=%s
    """
    params = [user_id, channel]

    if today_only:
        query += " AND date=%s"
        params.append(now().date().isoformat())

    rows = db_execute(query, tuple(params), fetch=True)
    total = 0 if not rows or rows[0][0] is None else rows[0][0]

    session = active_sessions.get(user_id)

    if session:
        start_time = session["start"]

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=KST)

        if session["channel"] == channel:
            if not today_only or start_time.date() == now().date():
                total += int((now() - start_time).total_seconds())

    return total


async def end_session(member):
    global last_log

    session = active_sessions.pop(member.id, None)
    if not session:
        return

    start = session["start"]
    if start.tzinfo is None:
        start = start.replace(tzinfo=KST)

    duration = int((now() - start).total_seconds())
    if duration <= 0:
        return

    user_id = session["user_id"]
    user_name = session["name"]
    channel = session["channel"]
    date = session["start"].date().isoformat()

    save_log(user_id, user_name, channel, duration, date)
    db_execute("DELETE FROM active_sessions WHERE user_id=%s", (user_id,))

    last_log = (user_id, user_name, channel, duration, date)

    total = get_time(user_id, channel, True)
    ch = get_text_channel(member.guild)

    await ch.send(
        f"{user_name} {channel} {format_time(duration)} 기록 (누적: {format_time(total)})"
    )


def start_session(member, channel_name, start_time):
    active_sessions[member.id] = {
        "start": start_time,
        "channel": channel_name,
        "name": member.display_name,
        "user_id": member.id
    }

    db_execute("""
        INSERT INTO active_sessions (user_id, user_name, channel, start)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE
        SET channel=%s, start=%s
    """, (
        member.id,
        member.display_name,
        channel_name,
        start_time,
        channel_name,
        start_time
    ))

# 타이머 / 알림
async def check_study_milestone(member):
    user = member.display_name
    alerts = study_alerts.setdefault(user, set())
    user_id = member.id

    study = get_time(user_id, STUDY_CHANNEL_NAME, True)
    ch = get_text_channel(member.guild)

    if study >= 3600 and 1 not in alerts:
        alerts.add(1)
        await ch.send(
            f"""{user} 1시간 집중 성곰! 🐻✨
조금 더 힘내라 곰!"""
        )

    if study >= 14400 and 4 not in alerts:
        alerts.add(4)
        await ch.send(
            f"""{user} 4시간 집중 성곰!! 🐻👍
이대로 8시간 가쟈 곰..!!"""
        )

    if study >= 28800 and 8 not in alerts:
        alerts.add(8)
        await ch.send(
            f"""{user} 8시간 집중 성곰!!! 🐻❤️
해냈다!! 오늘의 {user}는 엄청나다 곰..!!!"""
        )


async def study_timer(member):
    while member.id in active_sessions:
        session = active_sessions.get(member.id)

        if not session or session["channel"] != STUDY_CHANNEL_NAME:
            return

        await check_study_milestone(member)
        await discord.utils.sleep_until(now() + timedelta(minutes=1))


async def rest_warning(member):
    await discord.utils.sleep_until(now() + timedelta(hours=1))

    if member.id in active_sessions:
        session = active_sessions[member.id]

        if session["channel"] == REST_CHANNEL_NAME:
            ch = get_text_channel(member.guild)
            await ch.send(
                f"""{member.display_name}… 휴식이 너무 길다 곰. 🐻⌛
슬슬 돌아올 시간이다 곰!"""
            )


def create_channel_task(member, channel_name):
    if channel_name == STUDY_CHANNEL_NAME:
        task = bot.loop.create_task(study_timer(member))
        study_tasks[member.id] = task

    elif channel_name == REST_CHANNEL_NAME:
        task = bot.loop.create_task(rest_warning(member))
        rest_tasks[member.id] = task


async def handle_channel_entry(member, channel_name, send_study_start_message=False):
    started_at = now()
    start_session(member, channel_name, started_at)

    if channel_name == STUDY_CHANNEL_NAME and send_study_start_message:
        ch = get_text_channel(member.guild)
        await ch.send(
            f"{member.display_name} 공부 시작! 📚\n오늘 목표까지 달려보자 곰! 🐻🔥"
        )
        await check_study_milestone(member)

    create_channel_task(member, channel_name)


async def handle_channel_exit(member):
    cancel_task(study_tasks, member.id)
    cancel_task(rest_tasks, member.id)
    await end_session(member)

# 음성 상태 이벤트
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    before_name = before.channel.name if before.channel else None
    after_name = after.channel.name if after.channel else None

    entered_track_channel = before_name not in TRACK_CHANNELS and after_name in TRACK_CHANNELS
    moved_between_track_channels = before_name in TRACK_CHANNELS and after_name in TRACK_CHANNELS
    exited_track_channel = before_name in TRACK_CHANNELS and after_name not in TRACK_CHANNELS

    if entered_track_channel:
        await handle_channel_entry(
            member,
            after_name,
            send_study_start_message=(after_name == STUDY_CHANNEL_NAME)
        )

    elif moved_between_track_channels:
        await handle_channel_exit(member)
        await handle_channel_entry(member, after_name, send_study_start_message=False)

    elif exited_track_channel:
        await handle_channel_exit(member)

# 조회 명령어
async def send_user_time(ctx, user_name):
    member = get_member_by_display_name(ctx.guild, user_name)

    if not member:
        await ctx.send("사용자를 찾지 못했습니다.")
        return

    study = get_time(member.id, STUDY_CHANNEL_NAME, True)
    rest = get_time(member.id, REST_CHANNEL_NAME, True)

    await ctx.send(
        f"""{user_name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)} |"""
    )


@bot.command()
async def 지금(ctx):
    lines = []

    for name in TARGET_USERS:
        member = get_member_by_display_name(ctx.guild, name)
        if not member:
            continue

        study = get_time(member.id, STUDY_CHANNEL_NAME, True)
        rest = get_time(member.id, REST_CHANNEL_NAME, True)

        lines.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    if not lines:
        await ctx.send("대상 사용자를 찾지 못했다 곰")
        return

    await ctx.send("\n".join(lines))


@bot.command()
async def 멜마(ctx):
    await send_user_time(ctx, "멜마")


@bot.command()
async def 배키(ctx):
    await send_user_time(ctx, "배키")

# 삭제 / 복구
@bot.command()
async def 삭제(ctx):
    global last_log, last_deleted_log

    if not last_log:
        await ctx.send("삭제할 기록이 없다 곰")
        return

    user_id, user_name, channel, duration, date = last_log

    rows = db_execute("""
        SELECT id
        FROM study_logs
        WHERE user_id=%s AND channel=%s AND duration=%s AND date=%s
        ORDER BY id DESC
        LIMIT 1
    """, (user_id, channel, duration, date), fetch=True)

    if not rows:
        await ctx.send("삭제할 기록을 찾지 못했다 곰")
        return

    log_id = rows[0][0]
    db_execute("DELETE FROM study_logs WHERE id=%s", (log_id,))

    last_deleted_log = last_log
    last_log = None

    total = get_time(user_id, channel, True)

    await ctx.send(
        f"⛔기록 삭제 완료\n>> {user_name} {channel} {format_time(total)} (누적)"
    )


@bot.command()
async def 복구(ctx):
    global last_deleted_log, last_log

    if not last_deleted_log:
        await ctx.send("복구할 기록이 없다 곰")
        return

    user_id, user_name, channel, duration, date = last_deleted_log

    save_log(user_id, user_name, channel, duration, date)

    last_log = last_deleted_log
    last_deleted_log = None

    total = get_time(user_id, channel, True)

    await ctx.send(
        f"♻️기록 복구 완료\n>> {user_name} {channel} {format_time(duration)} (누적 {format_time(total)})"
    )

# 초기화
async def reset_user_today(ctx, user_name):
    global last_reset_backup

    member = get_member_by_display_name(ctx.guild, user_name)
    if not member:
        await ctx.send(f"{user_name} 사용자를 찾지 못했다 곰")
        return

    today = now().date().isoformat()

    last_reset_backup = db_execute("""
        SELECT user_id, user_name, channel, duration, date
        FROM study_logs
        WHERE user_id=%s AND date=%s
    """, (member.id, today), fetch=True)

    db_execute("""
        DELETE FROM study_logs
        WHERE user_id=%s AND date=%s
    """, (member.id, today))

    active_sessions.pop(member.id, None)
    db_execute("DELETE FROM active_sessions WHERE user_id=%s", (member.id,))

    study_alerts[user_name].clear()

    study = get_time(member.id, STUDY_CHANNEL_NAME, True)
    rest = get_time(member.id, REST_CHANNEL_NAME, True)

    await ctx.send(
        f"""⚠️ {user_name} 오늘 기록이 초기화되었다 곰
{user_name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"""
    )


@bot.command()
async def 멜마초기화(ctx):
    await reset_user_today(ctx, "멜마")


@bot.command()
async def 배키초기화(ctx):
    await reset_user_today(ctx, "배키")


@bot.command()
async def 초기화(ctx):
    global last_reset_backup

    today = now().date().isoformat()

    last_reset_backup = db_execute("""
        SELECT user_id, user_name, channel, duration, date
        FROM study_logs
        WHERE date=%s
    """, (today,), fetch=True)

    db_execute("DELETE FROM study_logs WHERE date=%s", (today,))

    msg = ["⚠️ 오늘 전체 기록이 초기화되었다 곰"]

    for name in TARGET_USERS:
        member = get_member_by_display_name(ctx.guild, name)
        if not member:
            continue

        study = get_time(member.id, STUDY_CHANNEL_NAME, True)
        rest = get_time(member.id, REST_CHANNEL_NAME, True)

        msg.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(msg))


@bot.command()
async def 초기화취소(ctx):
    global last_reset_backup

    if not last_reset_backup:
        await ctx.send("취소할 초기화 기록이 없다 곰")
        return

    for row in last_reset_backup:
        db_execute("""
            INSERT INTO study_logs (user_id, user_name, channel, duration, date)
            VALUES (%s, %s, %s, %s, %s)
        """, row)

    last_reset_backup = None

    msg = ["♻️ 초기화가 취소되었다 곰"]

    for name in TARGET_USERS:
        member = get_member_by_display_name(ctx.guild, name)
        if not member:
            continue

        study = get_time(member.id, STUDY_CHANNEL_NAME, True)
        rest = get_time(member.id, REST_CHANNEL_NAME, True)

        msg.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(msg))

# 명령어 에러
@bot.event
async def on_command_error(ctx, error):
    print("명령어 에러:", repr(error))
    await ctx.send(f"명령어 처리 중 오류가 났다 곰: {error}")

# 정시 메시지
@tasks.loop(minutes=1)
async def night_message():
    global last_night_message

    n = now()

    if n.hour != 23 or n.minute != 0:
        return

    if last_night_message == n.date():
        return

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    ch = get_text_channel(guild)
    await ch.send(
        """오늘도 공부 수고했다 곰!
이제 푹 쉬어라 곰 :) 🐻🌙"""
    )

    last_night_message = n.date()


@tasks.loop(minutes=1)
async def midnight_ranking():
    n = now()

    if n.hour != 0 or n.minute != 0:
        return

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    result = []

    for name in TARGET_USERS:
        member = get_member_by_display_name(guild, name)
        if not member:
            continue

        study = get_time(member.id, STUDY_CHANNEL_NAME, True)
        rest = get_time(member.id, REST_CHANNEL_NAME, True)

        result.append((name, study, rest))

    if not result:
        return

    study_rank = sorted(result, key=lambda x: x[1], reverse=True)
    rest_rank = sorted(result, key=lambda x: x[2], reverse=True)

    medals = ["🥇", "🥈"]
    rest_icons = ["🐢", "🦥"]

    msg = []
    msg.append("═══ 오늘의 공부 랭킹 ═══\n")

    for i, (name, study, _) in enumerate(study_rank):
        msg.append(f"{medals[i]} {name} : {format_time(study)}")

    msg.append("\n══════════════\n")
    msg.append("═══ 오늘의 휴식 랭킹 ═══\n")

    for i, (name, _, rest) in enumerate(rest_rank):
        msg.append(f"{rest_icons[i]} {name} : {format_time(rest)}")

    msg.append("\n══════════════")

    winner = study_rank[0][0]
    msg.append(f"\n오늘 공부왕은~~~ 👑{winner}! 축하한다 곰~! 🐻🎉")

    ch = get_text_channel(guild)
    await ch.send("\n".join(msg))

    study_alerts.clear()

# 봇 시작
@bot.event
async def on_ready():
    print("안녕! 난 Study_Gom이다 곰! 🐻")

    init_db()

    db_execute("DELETE FROM active_sessions")
    active_sessions.clear()

    if not midnight_ranking.is_running():
        midnight_ranking.start()

    if not night_message.is_running():
        night_message.start()

    rows = db_execute("""
        SELECT user_id, user_name, channel, start
        FROM active_sessions
    """, fetch=True)

    for user_id, user_name, channel, start in rows:
        if start.tzinfo is None:
            start = start.replace(tzinfo=KST)

        active_sessions[user_id] = {
            "user_id": user_id,
            "name": user_name,
            "channel": channel,
            "start": start
        }

# 헬스체크 서버
def fake_web_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"StudyGomBot alive")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


threading.Thread(target=fake_web_server, daemon=True).start()
bot.run(TOKEN)