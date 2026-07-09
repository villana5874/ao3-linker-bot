import discord
from discord.ext import commands
import re
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
import os
import asyncio
import cloudscraper
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

AO3_WORK = re.compile(r'(?:https?://)?(?:www\.)?archiveofourown\.org/works/(\d+)', re.IGNORECASE)
AO3_USER = re.compile(r'(?:https?://)?(?:www\.)?archiveofourown\.org/users/([\w-]+)', re.IGNORECASE)
AO3_SERIES = re.compile(r'(?:https?://)?(?:www\.)?archiveofourown\.org/series/(\d+)', re.IGNORECASE)
AO3_COLLECTION = re.compile(r'(?:https?://)?(?:www\.)?archiveofourown\.org/collections/([\w-]+)', re.IGNORECASE)
AO3_WORK_SHORT = re.compile(r'(?:https?://)?(?:www\.)?ao3\.org/works/(\d+)', re.IGNORECASE)
AO3_CHAPTER = re.compile(r'(?:https?://)?(?:www\.)?archiveofourown\.org/works/(\d+)/chapters/\d+', re.IGNORECASE)
AO3_CHAPTER_SHORT = re.compile(r'(?:https?://)?(?:www\.)?ao3\.org/works/(\d+)/chapters/\d+', re.IGNORECASE)

RATING_COLORS = {
    'Not Rated': 0x999999,
    'General Audiences': 0x00FF00,
    'Teen And Up Audiences': 0xFFA500,
    'Mature': 0xFF0000,
    'Explicit': 0x800080,
}
DEFAULT_COLOR = 0x999999


def clean(text):
    return ' '.join(text.split()).strip()


def truncate(text, max_len=500):
    if len(text) > max_len:
        return text[:max_len-3] + '...'
    return text


def rating_color(rating):
    return RATING_COLORS.get(rating, DEFAULT_COLOR)


scraper = cloudscraper.create_scraper()

async def fetch(url):
    try:
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, lambda: scraper.get(url, timeout=30))
        text = r.text
        soup = BeautifulSoup(text, 'html.parser')
        if soup.find('h2', class_='title') or soup.find('h2', class_='heading'):
            return soup
        if 'content warning' in text.lower() or 'adult content' in text.lower():
            r2 = await loop.run_in_executor(None, lambda: scraper.get(url, timeout=30))
            if r2.status_code == 200:
                return BeautifulSoup(r2.text, 'html.parser')
        return soup
    except Exception:
        return None


def meta_value(soup, label):
    for dt in soup.find_all('dt'):
        if label in dt.text:
            dd = dt.find_next_sibling('dd')
            if dd:
                a = dd.find('a')
                return clean(a.text) if a else clean(dd.text)
    return None


def meta_values(soup, label):
    for dt in soup.find_all('dt'):
        if label in dt.text:
            dd = dt.find_next_sibling('dd')
            if dd:
                return [clean(a.text) for a in dd.find_all('a')]
    return []


async def scrape_work(work_id):
    soup = await fetch(f'https://archiveofourown.org/works/{work_id}?view_adult=true')
    if not soup:
        return None

    title = soup.find('h2', class_='title')
    title = clean(title.text) if title else 'Unknown Title'

    author_tag = soup.find('a', rel='author')
    author = clean(author_tag.text) if author_tag else 'Unknown Author'
    author_url = f"https://archiveofourown.org{author_tag['href']}" if author_tag and author_tag.get('href') else None

    summary_div = soup.find('div', class_='summary')
    if summary_div:
        blockquote = summary_div.find('blockquote')
        summary = truncate(clean(blockquote.text)) if blockquote else ''
    else:
        summary = ''

    def join_tags(tags, max_len=500):
        result = ''
        for tag in tags:
            next_str = result + (', ' if result else '') + tag
            if len(next_str) > max_len:
                remaining = len(tags) - tags.index(tag)
                return result + f' (+{remaining} more)'
            result = next_str
        return result

    rating = meta_value(soup, 'Rating') or 'Not Rated'
    warning = join_tags(meta_values(soup, 'Archive Warning')) or 'None'
    category = join_tags(meta_values(soup, 'Category')) or 'Not Specified'
    fandoms = meta_values(soup, 'Fandom')
    fandom = fandoms[0] if fandoms else ''
    characters = join_tags(meta_values(soup, 'Character'), max_len=500)
    relationships = join_tags(meta_values(soup, 'Relationship'), max_len=500)
    tags = join_tags(meta_values(soup, 'Additional Tags'), max_len=1000)

    stats_dl = soup.find('dl', class_='stats')
    words = chapters = ''

    if stats_dl:
        for dt in stats_dl.find_all('dt'):
            label = clean(dt.text)
            dd = dt.find_next_sibling('dd')
            if dd:
                val = clean(dd.text)
                if 'Words' in label:
                    words = val
                elif 'Chapters' in label:
                    chapters = val

    embed = discord.Embed(title=title, url=f'https://archiveofourown.org/works/{work_id}', description=summary, color=rating_color(rating))
    embed.add_field(name='Rating', value=rating, inline=True)
    embed.add_field(name='Warning', value=warning, inline=True)
    embed.add_field(name='Category', value=category, inline=True)
    if fandom:
        embed.add_field(name='Fandom', value=fandom, inline=True)
    if relationships:
        embed.add_field(name='Relationships', value=relationships, inline=False)
    if characters:
        embed.add_field(name='Characters', value=characters, inline=False)
    if words:
        embed.add_field(name='Words', value=words, inline=True)
    if chapters:
        embed.add_field(name='Chapters', value=chapters, inline=True)
    if tags:
        embed.add_field(name='Tags', value=tags, inline=False)

    if author:
        embed.set_author(name=author, url=author_url)
    embed.set_footer(text='Archive of Our Own')

    return embed


async def scrape_user(username):
    soup = await fetch(f'https://archiveofourown.org/users/{username}')
    if not soup:
        return None

    heading = soup.find('h2', class_='heading')
    display = clean(heading.text) if heading else username

    works = meta_value(soup, 'Works') or '?'

    bio_tag = soup.find('div', class_='bio')
    bio = truncate(clean(bio_tag.text)) if bio_tag else ''

    embed = discord.Embed(title=display, url=f'https://archiveofourown.org/users/{username}', description=bio, color=0x0066CC)
    embed.add_field(name='Works', value=works, inline=True)
    embed.set_footer(text='Archive of Our Own')

    return embed


async def scrape_series(series_id):
    soup = await fetch(f'https://archiveofourown.org/series/{series_id}')
    if not soup:
        return None

    title_tag = soup.find('h2', class_='heading')
    title = clean(title_tag.text) if title_tag else 'Unknown Series'

    author_tag = soup.find('a', rel='author')
    author = clean(author_tag.text) if author_tag else None
    author_url = f"https://archiveofourown.org{author_tag['href']}" if author_tag and author_tag.get('href') else None

    desc_tag = soup.find('blockquote')
    desc = truncate(clean(desc_tag.text)) if desc_tag else ''

    works = meta_value(soup, 'Works') or '?'

    embed = discord.Embed(title=title, url=f'https://archiveofourown.org/series/{series_id}', description=desc, color=0x00CC99)
    embed.add_field(name='Works', value=works, inline=True)
    if author:
        embed.set_author(name=author, url=author_url)
    embed.set_footer(text='Archive of Our Own')

    return embed


async def scrape_collection(name):
    soup = await fetch(f'https://archiveofourown.org/collections/{name}')
    if not soup:
        return None

    title_tag = soup.find('h2', class_='heading')
    title = clean(title_tag.text) if title_tag else name

    desc_tag = soup.find('div', class_='intro')
    desc = truncate(clean(desc_tag.text)) if desc_tag else ''

    works = meta_value(soup, 'Works') or '?'

    embed = discord.Embed(title=title, url=f'https://archiveofourown.org/collections/{name}', description=desc, color=0xCC6600)
    embed.add_field(name='Works', value=works, inline=True)
    embed.set_footer(text='Archive of Our Own')

    return embed


@bot.event
async def on_ready():
    print(f'Bot is online! Logged in as {bot.user.name}')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='archiveofourown.org'))


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content

    for pattern, handler in [
        (AO3_CHAPTER, lambda m: scrape_work(m.group(1))),
        (AO3_CHAPTER_SHORT, lambda m: scrape_work(m.group(1))),
        (AO3_WORK, lambda m: scrape_work(m.group(1))),
        (AO3_WORK_SHORT, lambda m: scrape_work(m.group(1))),
        (AO3_USER, lambda m: scrape_user(m.group(1))),
        (AO3_SERIES, lambda m: scrape_series(m.group(1))),
        (AO3_COLLECTION, lambda m: scrape_collection(m.group(1))),
    ]:
        match = pattern.search(content)
        if match:
            async with message.channel.typing():
                embed = await handler(match)
                if embed:
                    await message.reply(embed=embed, mention_author=False)
                else:
                    await message.reply('Could not fetch info from AO3. The page may be private or AO3 may be unavailable.', mention_author=False)
            return


async def web_handler(request):
    return web.Response(text='AO3 Linker Bot is running!')

async def main():
    port = int(os.getenv('PORT', 8080))
    app = web.Application()
    app.router.add_get('/', web_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f'Web server started on port {port}')

    await bot.start(TOKEN)

if __name__ == '__main__':
    if not TOKEN:
        print('ERROR: No Discord token found!')
        print('Create a .env file in this folder with: DISCORD_TOKEN=your_token_here')
    else:
        asyncio.run(main())
