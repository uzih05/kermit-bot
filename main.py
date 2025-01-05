# main.py
from typing import Optional

import asyncio
import json
import logging
import os
import ssl
import aiohttp
import discord
from discord import Interaction, app_commands
from discord.ext import commands
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# 기존 핸들러 제거
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# 콘솔 핸들러 생성
stream_handler = logging.StreamHandler()

# 포매터 설정
console_formatter = logging.Formatter('%(levelname)s: %(message)s')  # 간단한 포맷

stream_handler.setFormatter(console_formatter)

# 콘솔 핸들러 추가
logger.addHandler(stream_handler)

# propagate 설정 (중복 로깅 방지)
logger.propagate = False

# .env 파일에서 환경 변수 로드
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

# 환경 변수 로드 및 확인
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    logger.error("DISCORD_TOKEN 환경 변수가 설정되지 않았습니다. .env 파일을 확인하세요.")
    raise ValueError("DISCORD_TOKEN 환경 변수가 누락되었습니다.")

# SSL 설정
ssl_context = ssl.create_default_context()

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self.session = None
        self._loaded_cogs = set()

    async def setup_hook(self):
        failed_cogs = []

        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename != '__init__.py':
                extension = f'cogs.{filename[:-3]}'
                if extension not in self._loaded_cogs:
                    try:
                        await self.load_extension(extension)
                        self._loaded_cogs.add(extension)
                    except Exception as e:
                        logger.error(f'{extension} load failed: {e}')
                        failed_cogs.append((extension, str(e)))

        if failed_cogs:
            # Try to reload failed cogs after a delay
            async def retry_failed_cogs():
                await asyncio.sleep(5)
                for extension, error in failed_cogs:
                    try:
                        await self.load_extension(extension)
                        self._loaded_cogs.add(extension)
                        logger.info(f'Successfully reloaded {extension}')
                    except Exception as e:
                        logger.error(f'Retry loading {extension} failed: {e}')

            self.loop.create_task(retry_failed_cogs())

    async def on_ready(self):
        activities = [
            discord.Game(name="/도움말"),
            discord.Activity(type=discord.ActivityType.listening, name="명령어"),
            discord.Game(name="문의: example@mail.com")
        ]

        async def rotate_activity():
            idx = 0
            while True:
                activity = activities[idx]
                await self.change_presence(status=discord.Status.online, activity=activity)
                idx = (idx + 1) % len(activities)
                await asyncio.sleep(300)  # 5 minutes

        self.loop.create_task(rotate_activity())

    async def close(self):
        """Graceful shutdown with cleanup"""
        try:
            # Clean up session
            if self.session and not self.session.closed:
                await self.session.close()

            # Clean up cogs
            for extension in list(self._loaded_cogs):
                try:
                    await self.unload_extension(extension)
                except Exception as e:
                    logger.error(f'Extension {extension} unload failed: {e}')

            await super().close()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

# 봇 객체 생성
bot = MyBot()

# 전역 예외 핸들러 추가 (메인 스크립트에 위치)
@bot.event
async def on_error(event, *args, **kwargs):
    logger.exception(f"Unhandled exception in event {event}")

# 슬래시 명령어 에러 핸들러
@bot.tree.error
async def on_app_command_error(interaction: Interaction, error: app_commands.AppCommandError):
    try:
        if isinstance(error, app_commands.CommandOnCooldown):
            retry_after = round(error.retry_after)
            await interaction.response.send_message(
                f"명령어 쿨다운: {retry_after}초 남음",
                ephemeral=True
            )
        elif isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "권한이 없거나 이 채널에서 사용할 수 없는 명령어입니다.",
                ephemeral=True
            )
        elif isinstance(error, app_commands.CommandInvokeError):
            # Log the full error traceback
            logger.exception("Command error", exc_info=error.original)

            error_msg = "명령어 실행 중 오류가 발생했습니다."
            if isinstance(error.original, discord.HTTPException):
                error_msg = "Discord API 오류가 발생했습니다."
            elif isinstance(error.original, asyncio.TimeoutError):
                error_msg = "요청 시간이 초과되었습니다."

            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)
    except Exception as e:
        logger.exception(f"Error handler failed: {e}")

# 슬래시 명령어: 도움말
@bot.tree.command(
    name="도움말",
    description="기능 그룹 목록 또는 특정 기능의 상세 도움말을 확인합니다."
)
@app_commands.describe(
    category="도움말을 볼 기능 카테고리 (예: 관리, 곡 등)"
)
async def help_command(interaction: Interaction, category: Optional[str] = None):
    try:
        await interaction.response.defer()

        if not category:
            # 전체 기능 그룹 및 특수 명령어 목록 표시
            embed = discord.Embed(
                title="🔍 도움말: 기능 및 명령어 목록",
                description="사용 가능한 모든 기능 그룹 및 특수 명령어입니다.\n각 기능의 자세한 설명을 보려면 `/도움말 [카테고리]`를 입력하세요.",
                color=discord.Color.blue()
            )

            # 그룹 명령어 목록 추가
            for command in bot.tree.get_commands():
                if isinstance(command, app_commands.Group):
                    embed.add_field(
                        name=f"📎 {command.name.capitalize()}",
                        value=f"`/도움말 {command.name}`으로 자세히 보기",
                        inline=False
                    )

            # 특수 명령어 목록 추가
            standalone_commands = [
                cmd for cmd in bot.tree.get_commands() if not isinstance(cmd, app_commands.Group)
            ]
            if standalone_commands:
                special_commands = "\n".join(
                    [f"/{cmd.name} - {cmd.description}" for cmd in standalone_commands]
                )
                embed.add_field(
                    name="--특수 명령어--",
                    value=special_commands,
                    inline=False
                )

            await interaction.followup.send(embed=embed)
            return

        # 특정 카테고리 도움말 표시
        category = category.lower()
        group = bot.tree.get_command(category)

        if not group or not isinstance(group, app_commands.Group):
            await interaction.followup.send(
                f"❌ '{category}' 카테고리를 찾을 수 없습니다.\n사용 가능한 카테고리 목록을 보려면 `/도움말`을 입력하세요.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📚 {category.capitalize()} 카테고리 도움말",
            description=f"{category.capitalize()} 카테고리에서 사용할 수 있는 모든 명령어입니다.",
            color=discord.Color.blue()
        )

        for subcommand in group.commands:
            name = f"/{group.name} {subcommand.name}"
            params = []
            for param in subcommand.parameters:
                param_desc = f"[{param.name}]" if param.required else f"({param.name})"
                params.append(param_desc)

            usage = f"{name} {' '.join(params)}"
            value = f"💡 {subcommand.description}"
            if params:
                value += f"\n```사용법: {usage}```"

            embed.add_field(name=usage, value=value, inline=False)

        embed.set_footer(text="[] = 필수 항목, () = 선택 항목")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"도움말 명령어 실행 중 오류 발생: {e}")
        await interaction.followup.send(
            "도움말을 불러오는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            ephemeral=True
        )

async def main():
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"봇 실행 중 예기치 않은 오류 발생: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"프로그램 실행 중 예기치 않은 오류 발생: {e}")