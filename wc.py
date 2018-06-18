from dateutil import parser
from datetime import datetime, timedelta
import aiohttp
import asyncio
import json
import logging
import os
import time

def num_to_word(num):
    if num == 0: return ':zero:'
    if num == 1: return ':one:'
    if num == 2: return ':two:'
    if num == 3: return ':three:'
    if num == 4: return ':four:'
    if num == 5: return ':five:'
    if num == 6: return ':six:'
    if num == 7: return ':seven:'
    if num == 8: return ':eight:'
    if num == 9: return ':nine:'
    if num == 10: return ':ten:'
    return num

def code_to_flag(code):
    if code == 'EGY': return ':flag-eg:' # egypt
    if code == 'RUS': return ':flag-ru:' # russia
    if code == 'KSA': return ':flag-sa:' # saudi arabia
    if code == 'URU': return ':flag-uy:' # uruguay

    if code == 'IRN': return ':flag-ir:' # iran
    if code == 'MAR': return ':flag-ma:' # morocco
    if code == 'POR': return ':flag-pt:' # portugal
    if code == 'ESP': return ':flag-es:' # spain

    if code == 'AUS': return ':flag-au:' # australia
    if code == 'DEN': return ':flag-dk:' # denmark
    if code == 'FRA': return ':flag-fr:' # france
    if code == 'PER': return ':flag-pe:' # peru

    if code == 'ARG': return ':flag-ar:' # argentina
    if code == 'CRO': return ':flag-hr:' # croatia
    if code == 'ISL': return ':flag-is:' # iceland
    if code == 'NGA': return ':flag-ng:' # nigeria

    if code == 'BRA': return ':flag-br:' # brazil
    if code == 'CRC': return ':flag-cr:' # costa rica
    if code == 'SRB': return ':flag-rs:' # serbia
    if code == 'SUI': return ':flag-ch:' # switzerland

    if code == 'GER': return ':flag-de:' # germany
    if code == 'MEX': return ':flag-mx:' # mexico
    if code == 'KOR': return ':flag-kr:' # south korea
    if code == 'SWE': return ':flag-se:' # sweden

    if code == 'BEL': return ':flag-be:' # belgium
    if code == 'ENG': return ':flag-england:' # england
    if code == 'PAN': return ':flag-pa:' # panama
    if code == 'TUN': return ':flag-tn:' # tunisia

    if code == 'COL': return ':flag-co:' # colombia
    if code == 'JPN': return ':flag-jp:' # japan
    if code == 'POL': return ':flag-pl:' # poland
    if code == 'SEN': return ':flag-sn:' # senegal

    return code

class WorldCupSlackReporter:
    def __init__(self):
        self.today_url = 'http://worldcup.sfg.io/matches/today'

        self.sem = asyncio.Semaphore(5)
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False))
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.filepath = os.path.abspath(os.path.dirname(__file__))
        self.slack_instances = []
        self.slack_payload = None
        self.update_rate = 90

        self.matches = {}
        self.event_types = {
            'goal-own': '[flag] [country]: Oh no, [player] just scored a goal on the wrong side of the field! :face_palm:',
            'yellow-card': '[flag] [country]: [player] just received a yellow card :warning:',
            'red-card': '[flag] [country]: [player] just received a red card :rotating_light:',
            'goal': '[flag] [country]: [player] just scored a goooooooal! :soccer:',
            'goal-penalty': '[flag] [country]: [player] gets a goal penalty :dart:'
        }

    async def api_get(self, url):
        async def _get(url):
            try:
                async with self.sem, self.session.get(url) as response:
                    return await response.read(), response.status
            except aiohttp.client_exceptions.ClientConnectorError as e:
                self.logger.error(e)
                return e, 999
        response = await _get(url)
        if response[1] != 200:
            raise ConnectionError(f'did not get a 200 response: {response[0]}')
        with open(os.path.join(self.filepath, 'match-requests.log'), 'a+') as logfile:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data = json.dumps(json.loads(response[0]))
            logfile.write(f'{now}: {data}\n')
        return json.loads(response[0])

    async def get_todays_matches(self):
        try:
            matches = await self.api_get(self.today_url)
        except ConnectionError as e:
            self.logger.error(e)
        message = 'Today\'s matches:\n'
        for match in matches:
            hteam = match.get('home_team').get('country')
            hcode = match.get('home_team').get('code')
            hflag = code_to_flag(hcode)
            ateam = match.get('away_team').get('country')
            acode = match.get('away_team').get('code')
            aflag = code_to_flag(acode)
            venue = match.get('location') + ', ' + match.get('venue')
            start_time = parser.parse(match.get('datetime')).strftime('%H:%M')
            match_id = match.get('home_team').get('code') + match.get('away_team').get('code')
            if match_id not in self.matches:
                self.matches[match_id] = {
                    'score': 0,
                    'goals': {'h': 0, 'a': 0},
                    'event_ids': [],
                    'status': 0,
                    'time': None,
                    'half-time': False
                }
            message += f':timer_clock: {start_time}: {hflag} {hteam} vs {aflag} {ateam} @ {venue}\n'
        asyncio.ensure_future(self._slack_output(message.rstrip()))

    async def get_current_matches(self):
        try:
            matches = await self.api_get(self.today_url)
        except ConnectionError as e:
            self.logger.error(e)
        except json.decoder.JSONDecodeError as e:
            self.logger.error(e)

        try:
            for match in matches:
                if not self.matches or self.matches is None:
                    continue
                message = ''
                hteam = match.get('home_team').get('country')
                hteamgoals = match.get('home_team').get('goals')
                hgoals = num_to_word(hteamgoals)
                hflag = code_to_flag(match.get('home_team').get('code'))

                ateam = match.get('away_team').get('country')
                ateamgoals = match.get('away_team').get('goals')
                agoals = num_to_word(ateamgoals)
                aflag = code_to_flag(match.get('away_team').get('code'))

                score = hteamgoals + ateamgoals
                match_id = match.get('home_team').get('code') + match.get('away_team').get('code')
                if hteamgoals < self.matches.get(match_id).get('goals').get('h'):
                    hteamgoals = self.matches.get(match_id).get('goals').get('h')
                if ateamgoals < self.matches.get(match_id).get('goals').get('a'):
                    ateamgoals = self.matches.get(match_id).get('goals').get('a')

                if match.get('status') == 'in progress' and self.matches.get(match_id).get('status') == 0:
                    message += f'{hteam} vs {ateam} just started!\n'
                    self.matches[match_id]['status'] = 1
                    self.matches[match_id]['time'] = time.time()

                if self.matches.get(match_id).get('status') == 2:
                    continue
                for item in match.get('home_team_events'):
                    item['code'] = match.get('home_team').get('code')
                    item['flag'] = hflag
                    item['country'] = hteam
                for item in match.get('away_team_events'):
                    item['code'] = match.get('away_team').get('code')
                    item['flag'] = aflag
                    item['country'] = ateam
                events = match.get('home_team_events') + match.get('away_team_events')
                for eid in sorted(events, key=lambda x: x.get('id')):
                    if eid.get('id') in self.matches.get(match_id).get('event_ids'):
                        continue
                    self.matches[match_id]['event_ids'].append(eid.get('id'))
                    event_text = self.event_types.get(eid.get('type_of_event'), '').replace('[player]', eid.get('player')).replace('[country]', eid.get('country')).replace('[flag]', eid.get('flag'))
                    if event_text == '':
                        continue
                    event_text = ':stopwatch: ' + eid.get('time') + ' ' + event_text
                    message += f'{event_text}\n'
                if match.get('time') == 'half-time' and not self.matches.get(match_id).get('half-time'):
                    self.matches[match_id]['half-time'] = True
                    message += f':stopwatch: Half-time: {hflag} {hteam} {hgoals} vs {agoals} {aflag} {ateam}\n'
                if score > self.matches.get(match_id).get('score'):
                    message += f':recycle: Score update: {hflag} {hteam} {hgoals} vs {agoals} {aflag} {ateam}\n'
                    self.matches[match_id]['score'] = score
                if match.get('status') == 'completed' or match.get('winner') or match.get('time') == 'full-time':
                    message += f':checkered_flag: Match ended! Final score:\n{hflag} {hteam} {hgoals} vs {agoals} {aflag} {ateam}\n'
                    self.matches[match_id]['status'] = 2
                if self.matches.get(match_id).get('status') == 1:
                    timediff = time.time() - self.matches.get(match_id).get('time')
                    if timediff > 9000:
                        message += f':checkered_flag: Match (probably) ended (2h since start)! Final score:\n{hflag} {hteam} {hgoals} - {agoals} {aflag} {ateam}\n'
                        self.matches[match_id]['status'] = 2
                asyncio.ensure_future(self._slack_output(message.rstrip()))
        except:
            pass

    async def monitor(self):
        asyncio.ensure_future(self.get_current_matches())
        await asyncio.sleep(self.update_rate)
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
    await WCS.get_todays_matches()
    await asyncio.sleep(5)
    asyncio.ensure_future(WCS.monitor())


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.run_forever()
