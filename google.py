from bs4 import BeautifulSoup as BS
from datetime import datetime, timedelta
import aiohttp
import asyncio
import json
import logging
import os
import random


class WorldCupSlackReporter:
    def __init__(self):
        self.today_url = 'https://www.google.se/search?q=world+cup+today'
        self.headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}
        self.hours_to_add = 0
        self.matches = {}
        self.sleep = 5

        self.sem = asyncio.Semaphore(5)
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False))
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.filepath = os.path.abspath(os.path.dirname(__file__))

        self.slack_instances = []
        self.slack_payload = None

    async def url_get(self, url):
        async def _get(url):
            try:
                async with self.sem, self.session.get(url, headers=self.headers) as response:
                    return await response.read(), response.status
            except aiohttp.client_exceptions.ClientConnectorError as e:
                self.logger.error(e)
                return e, 999
        response = await _get(url)
        if response[1] != 200:
            raise ConnectionError(f'did not get a 200 response: {response[0]}')
        the_page = BS(response[0], 'html.parser')
        return the_page

    @staticmethod
    def get_info(match, conlist):
        for i in conlist:
            match = match.contents[i]
        return match.text

    async def get_todays_matches(self):
        try:
            page = await self.url_get(self.today_url)
        except ConnectionError as e:
            self.logger.error(e)
            return
        matches = page.findAll('div', class_='imspo_mt__mtc-no')
        message = 'Today\'s matches:\n'
        for match in matches:
            status = 0
            match = match.contents[0]
            hteam = self.get_info(match, [2, 1, 1, 0])
            hteamgoals = self.get_info(match, [2, 1, 0])
            ateam = self.get_info(match, [4, 1, 1, 0])
            ateamgoals = self.get_info(match, [4, 1, 0])
            match_type = self.get_info(match, [1, 0, 0, 2])
            try:
                int(hteamgoals)
            except Exception:
                hteamgoals = '0'
            try:
                int(ateamgoals)
            except Exception:
                ateamgoals = '0'
            try:
                when = match.contents[0].contents[4].contents[0].contents[0].contents[0].contents
                when = when[0].text, when[1].text
            except Exception as e:
                when = ('Today', 'Already started') if 'ft' not in self.get_info(match, [0, 4, 0]).lower() else ('Today', 'Already ended')
                status = 1 if 'started' in when[1] else 2
            if when[0] not in ('Idag', 'Today'):
                continue
            start_time = (datetime.strptime(when[1], '%H:%M') + timedelta(hours=self.hours_to_add)).strftime('%H:%M') if 'Already' not in when[1] else when[1]
            match_id = hteam + ateam
            if match_id not in self.matches:
                self.matches[match_id] = {
                    'score': f'{hteamgoals} - {ateamgoals}',
                    'event_ids': [],
                    'status': status,
                    'hteam': hteam,
                    'ateam': ateam,
                    'half-time': False
                }
            message += f'*{start_time}*: {hteam} vs {ateam} ({match_type})\n'
        asyncio.ensure_future(self._slack_output(message.rstrip()))

    async def get_current_matches(self):
        try:
            page = await self.url_get(self.today_url)
        except ConnectionError as e:
            self.logger.error(e)
            return
        matches = page.findAll('div', class_='imspo_mt__mtc-no')
        local_matches = []
        for match in matches:
            match = match.contents[0]
            message = ''
            hteam = self.get_info(match, [2, 1, 1, 0])
            hteamgoals = self.get_info(match, [2, 1, 0])
            ateam = self.get_info(match, [4, 1, 1, 0])
            ateamgoals = self.get_info(match, [4, 1, 0])
            try:
                int(hteamgoals)
            except Exception:
                hteamgoals = '0'
            try:
                int(ateamgoals)
            except Exception:
                ateamgoals = '0'
            match_id = hteam + ateam
            if match_id not in self.matches:
                continue
            local_matches.append(match_id)
            try:
                status = self.get_info(match, [0, 4, 0, 1, 0])
            except Exception:
                status = ''
            try:
                status += self.get_info(match, [0, 4, 0, 1, 2])
            except Exception:
                status = status
            try:
                status += self.get_info(match, [0, 4, 0, 3, 0])
            except Exception:
                status = status
            status = status.lower()
            score = f'{hteamgoals} - {ateamgoals}'

            if any(x in status.lower() for x in ('live', 'pågår')) and self.matches.get(match_id).get('status') == 0:
                message += f'{hteam} vs {ateam} just started!\n'
                self.matches[match_id]['status'] = 1

            if self.matches.get(match_id).get('status') in (0, 2):
                continue

            if any(x in status for x in ('half–time', 'halvtid', 'ht', 'half')) and not self.matches.get(match_id).get('half-time'):
                self.matches[match_id]['half-time'] = True
                message += f'Half-time: {hteam} {hteamgoals} vs {ateamgoals} {ateam}\n'

            if score != self.matches.get(match_id).get('score'):
                message += f'GOOOOOOOAL!\n{hteam} {hteamgoals} - {ateamgoals} {ateam}\n'
                self.matches[match_id]['score'] = score

            if any(x in status for x in ('ended', 'full-time', 'ft', 'full')):
                message += f'Match ended! Final score:\n{hteam} {hteamgoals} - {ateamgoals} {ateam}\n'
                self.matches[match_id]['status'] = 2
            asyncio.ensure_future(self._slack_output(message.rstrip()))

        for key, value in self.matches.items():
            if value.get('status') in (0, 2):
                continue
            if key not in local_matches:
                asyncio.ensure_future(self._slack_output(f'Match ended! Final score:\n{value.get("hteam")} {score} {value.get("ateam")}'))
                self.matches[key]['status'] = 2

    async def monitor(self):
        asyncio.ensure_future(self.get_current_matches())
        await asyncio.sleep(random.choice(range(55, 87)))
        asyncio.ensure_future(self.monitor())

    async def _slack_output(self, message):
        async def _send(url, output):
            try:
                async with self.sem, self.session.post(url, data=output) as response:
                    return await response.read(), response.status
            except aiohttp.client_exceptions.ClientConnectorError as e:
                self.logger.error(e)
        for si in self.slack_instances:
            output = dict(self.slack_payload)
            output['text'] = message
            output['channel'] = si.get('channel')
            asyncio.ensure_future(_send(si.get('webhook'), json.dumps(output)))


async def main():
    WCS = WorldCupSlackReporter()
    with open(os.path.join(WCS.filepath, 'settings.json'), 'r') as settings_file:
        settings = json.loads(settings_file.read())
        WCS.slack_instances = settings.get('slack_instances')
        WCS.slack_payload = settings.get('slack_payload')
        WCS.hours_to_add = settings.get('hours_to_add') if settings.get('hours_to_add') else 0
    await WCS.get_todays_matches()
    await asyncio.sleep(WCS.sleep)
    asyncio.ensure_future(WCS.monitor())


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_forever()
    loop.close()
