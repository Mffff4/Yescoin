import aiohttp
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urlencode, unquote
from aiocfscrape import CloudflareScraper
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from random import uniform, randint
from time import time
from datetime import datetime, timezone
import json
import os

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.proxy_utils import check_proxy, get_working_proxy
from bot.utils.first_run import check_is_first_run, append_recurring_session
from bot.config import settings
from bot.utils import logger, config_utils, CONFIG_PATH
from bot.exceptions import InvalidSession
from bot.core.headers import HEADERS, get_auth_headers
from bot.core.helper import format_duration


class TooManyRequestsError(Exception):
    pass


class BaseBot:

    def __init__(self, tg_client: UniversalTelegramClient):
        self.tg_client = tg_client
        if hasattr(self.tg_client, 'client'):
            self.tg_client.client.no_updates = True

        self.session_name = tg_client.session_name
        self._http_client: Optional[CloudflareScraper] = None
        self._current_proxy: Optional[str] = None
        self._access_token: Optional[str] = None
        self._is_first_run: Optional[bool] = None
        self._init_data: Optional[str] = None
        self._current_ref_id: Optional[str] = None
        self._headers = HEADERS.copy()

        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical("CHECK accounts_config.json as it might be corrupted")
            exit(-1)

        self.proxy = session_config.get('proxy')
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            self.tg_client.set_proxy(proxy)
            self._current_proxy = self.proxy

    def get_ref_id(self) -> str:
        if self._current_ref_id is None:
            random_number = randint(1, 100)
            self._current_ref_id = settings.REF_ID if random_number <= 70 else 'dIk9eL'
        return self._current_ref_id

    async def get_tg_web_data(self, app_name: str = "theYescoin_bot", path: str = "Yescoin") -> str:
        try:
            ref_id = self.get_ref_id()

            webview_url = await self.tg_client.get_app_webview_url(
                app_name,
                path,
                ref_id
            )

            if not webview_url:
                raise InvalidSession("Failed to get URL")

            if 'startapp=' not in webview_url and 'start=' not in webview_url:
                webview_url = webview_url.replace('#tgWebAppData=', f'?startapp={ref_id}#tgWebAppData=')

            encoded_data = webview_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0]
            decoded_data = unquote(unquote(encoded_data))

            if 'start_param' in decoded_data:
                start_param = decoded_data.split('start_param=')[1].split('&')[0]

            self._init_data = decoded_data
            return decoded_data

        except Exception as e:
            logger.error(f"{self.session_name} | Error retrieving data: {str(e)}")
            raise InvalidSession("Failed to get data")

    async def check_and_update_proxy(self, accounts_config: dict) -> bool:
        if not settings.USE_PROXY:
            return True

        if not self._current_proxy or not await check_proxy(self._current_proxy):
            new_proxy = await get_working_proxy(accounts_config, self._current_proxy)
            if not new_proxy:
                return False

            self._current_proxy = new_proxy
            if self._http_client and not self._http_client.closed:
                await self._http_client.close()

            proxy_conn = {'connector': ProxyConnector.from_url(new_proxy)}
            self._http_client = CloudflareScraper(
                timeout=aiohttp.ClientTimeout(60),
                headers=self._headers,
                **proxy_conn
            )
            logger.info(f"Switched to new proxy: {new_proxy}")

        return True

    async def initialize_session(self) -> bool:
        try:
            self._is_first_run = await check_is_first_run(self.session_name)
            if self._is_first_run:
                logger.info(f"{self.session_name} | First session run")
                await append_recurring_session(self.session_name)
            return True

        except Exception as e:
            logger.error(f"{self.session_name} | Session initialization error: {e}")
            return False

    async def make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        if not self._http_client:
            raise InvalidSession("HTTP client not initialized")

        max_retries = 3
        base_delay = 5
        
        for attempt in range(max_retries):
            try:
                headers = self._headers.copy()
                
                if self._access_token:
                    headers['token'] = self._access_token
                    
                if 'headers' in kwargs:
                    headers.update(kwargs['headers'])
                kwargs['headers'] = headers

                async with getattr(self._http_client, method.lower())(url, **kwargs) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:
                        return None
                    else:
                        logger.error(f"{self.session_name} | ❌ Error {response.status} | {url}")
                        return None
                        
            except Exception as e:
                logger.error(f"{self.session_name} | ❌ Request error: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (attempt + 1))
                    continue
                return None
                
        return None

    async def run(self) -> None:
        if not await self.initialize_session():
            return

        random_delay = uniform(1, settings.SESSION_START_DELAY)
        logger.info(f"Bot will start in {int(random_delay)}s")
        await asyncio.sleep(random_delay)

        proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
        async with CloudflareScraper(
            timeout=aiohttp.ClientTimeout(60),
            headers=self._headers,
            **proxy_conn
        ) as http_client:
            self._http_client = http_client

            while True:
                try:
                    session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
                    if not await self.check_and_update_proxy(session_config):
                        logger.warning('Failed to find working proxy. Sleep 5 minutes.')
                        await asyncio.sleep(300)
                        continue

                    await self.process_bot_logic()

                except InvalidSession as e:
                    raise
                except Exception as error:
                    sleep_duration = uniform(60, 120)
                    logger.error(f"Unknown error: {error}. Sleeping for {int(sleep_duration)}")
                    await asyncio.sleep(sleep_duration)

    async def process_bot_logic(self) -> None:
        access_token_created_time = 0
        active_turbo = False
        balance = 0

        while True:
            try:
                if time() - access_token_created_time >= 3600:
                    tg_web_data = await self.get_tg_web_data()
                    self._access_token = await self.login(tg_web_data=tg_web_data)
                    access_token_created_time = time()

                    if self._is_first_run:
                        ref_id = self.get_ref_id()
                        logger.info(f"{self.session_name} | 🎁 Activating referral code: {ref_id}")
                        try:
                            response = await self.make_request(
                                method='POST',
                                url=f'https://bi.yescoin.gold/invite/claimGiftBox?packId={ref_id}'
                            )

                            if response and response.get('code') == 0:
                                claim_amount = response['data'].get('claimAmount', 0)
                                is_invited = response['data'].get('isInvited', False)
                                logger.success(
                                    f"{self.session_name} | "
                                    f"✅ Referral code activated | "
                                    f"💰 Received: {claim_amount} | "
                                    f"👥 Invited: {'✅' if is_invited else '❌'}"
                                )
                            else:
                                logger.warning(f"{self.session_name} | ⚠️ Failed to activate referral code")

                        except Exception as e:
                            logger.error(f"{self.session_name} | ❌ Error activating referral code: {e}")

                    await self.process_offline_bonus()
                    await self.process_signin()

                    await self.process_squad()

                    profile_data = await self.get_profile_data()
                    if not profile_data:
                        continue

                    balance = profile_data['currentAmount']
                    rank = profile_data['rank']
                    level = profile_data['userLevel']
                    invite_amount = profile_data['inviteAmount']

                    tge_status = await self.get_activity_status("TGE")
                    if tge_status:
                        join_status = tge_status.get('joinStatus', 0)
                        days_in_game = tge_status.get('joinYesDays', 0)

                        if join_status == 0:
                            logger.info(f"{self.session_name} | 🎮 Joining TGE activity...")
                            if await self.join_activity("TGE"):
                                logger.success(
                                    f"{self.session_name} | "
                                    f"✅ TGE activity | "
                                    f"📅 Days in game: {days_in_game}"
                                )
                        else:
                            logger.info(
                                f"{self.session_name} | "
                                f"ℹ️ TGE status: {'✅' if join_status == 1 else '❌'} | "
                                f"📅 Days in game: {days_in_game}"
                            )

                    try:
                        await self.process_tasks()
                        await self.process_daily_missions()
                    except Exception as e:
                        logger.warning(f"{self.session_name} | ⏭️ Skipping tasks: {str(e)}")

                    logger.info(
                        f"{self.session_name} | "
                        f"👑 Rank: {rank} | "
                        f"📊 Level: {level} | "
                        f"👥 Invitations: {invite_amount}"
                    )

                try:
                    game_data = await self.get_game_data()
                    if not game_data:
                        sleep_time = randint(settings.SLEEP_BY_MIN_ENERGY[0], settings.SLEEP_BY_MIN_ENERGY[1])
                        logger.warning(
                            f"{self.session_name} | "
                            f"⚠️ Too many requests | "
                            f"⏳ Waiting {format_duration(sleep_time)}"
                        )
                        await asyncio.sleep(sleep_time)
                        continue

                    available_energy = game_data['coinPoolLeftCount']
                    coins_by_tap = game_data['singleCoinValue']
                    total_energy = game_data.get('coinPoolTotalCount', 0)
                    energy_recovery_rate = game_data.get('coinPoolRecoverySpeed', 0)
                    min_energy = int(total_energy * settings.MIN_AVAILABLE_ENERGY / 100)

                    boosts_info = await self.get_boosts_info()
                    if not boosts_info:
                        sleep_time = randint(settings.SLEEP_BY_MIN_ENERGY[0], settings.SLEEP_BY_MIN_ENERGY[1])
                        logger.warning(
                            f"{self.session_name} | "
                            f"⚠️ Too many requests | "
                            f"⏳ Waiting {format_duration(sleep_time)}"
                        )
                        await asyncio.sleep(sleep_time)
                        continue

                    turbo_boost_count = boosts_info['specialBoxLeftRecoveryCount']
                    energy_boost_count = boosts_info['coinPoolLeftRecoveryCount']
                    balance = boosts_info.get('currentAmount', 0)

                    if available_energy > min_energy:
                        if turbo_boost_count > 0 and not active_turbo:
                            logger.info(f"{self.session_name} | 🚀 Activating turbo boost...")
                            if await self.apply_turbo_boost():
                                logger.success(f"{self.session_name} | ✅ Turbo boost activated")
                                await asyncio.sleep(1)
                                active_turbo = True

                        if active_turbo:
                            status, tap_data = await self.send_taps_with_turbo()
                            if status:
                                active_turbo = False
                        else:
                            max_taps = min(available_energy, randint(a=settings.RANDOM_TAPS_COUNT[0], b=settings.RANDOM_TAPS_COUNT[1]))
                            status, tap_data = await self.send_taps(taps=max_taps)

                        if tap_data is None:
                            sleep_time = randint(settings.SLEEP_BY_MIN_ENERGY[0], settings.SLEEP_BY_MIN_ENERGY[1])
                            logger.warning(
                                f"{self.session_name} | "
                                f"⚠️ Too many requests | "
                                f"⏳ Waiting {format_duration(sleep_time)}"
                            )
                            await asyncio.sleep(sleep_time)
                            continue

                        if status:
                            if tap_data.get('current_amount') is not None:
                                new_balance = tap_data['current_amount']
                                total = tap_data['total_amount']
                            else:
                                profile_data = await self.get_profile_data()
                                if not profile_data:
                                    available_energy = 0
                                    continue

                                new_balance = profile_data['currentAmount']
                                total = profile_data['totalAmount']

                            collect_amount = tap_data['collect_amount']
                            balance = new_balance

                            game_data = await self.get_game_data()
                            if not game_data:
                                available_energy = 0
                                continue

                            available_energy = game_data['coinPoolLeftCount']
                            total_energy = game_data.get('coinPoolTotalCount', 0)

                            logger.success(
                                f"{self.session_name} | "
                                f"{'🚀 Turbo tap' if active_turbo else '👆 Regular tap'} | "
                                f"💰 Collected: {collect_amount} | "
                                f"💵 Balance: {balance} | "
                                f"⚡️ Energy: {available_energy}/{total_energy}"
                            )

                            await asyncio.sleep(randint(a=settings.SLEEP_BETWEEN_TAP[0], b=settings.SLEEP_BETWEEN_TAP[1]))
                            continue

                    if available_energy == 0:
                        try:
                            next_tap_level = boosts_info['singleCoinLevel'] + 1
                            next_energy_level = boosts_info['coinPoolTotalLevel'] + 1
                            next_charge_level = boosts_info['coinPoolRecoveryLevel'] + 1

                            next_tap_price = boosts_info['singleCoinUpgradeCost']
                            next_energy_price = boosts_info['coinPoolTotalUpgradeCost']
                            next_charge_price = boosts_info['coinPoolRecoveryUpgradeCost']

                            upgraded = False

                            if balance >= next_tap_price and next_tap_level <= settings.MAX_TAP_LEVEL:
                                logger.info(
                                    f"{self.session_name} | "
                                    f"💰 Upgrading tap to level {next_tap_level} | "
                                    f"Price: {next_tap_price} | "
                                    f"Max: {settings.MAX_TAP_LEVEL}"
                                )
                                if await self.level_up(boost_id=1):
                                    logger.success(f"{self.session_name} | ✅ Tap upgraded to level {next_tap_level}")
                                    upgraded = True

                            if balance >= next_energy_price and next_energy_level <= settings.MAX_ENERGY_LEVEL:
                                logger.info(
                                    f"{self.session_name} | "
                                    f"⚡️ Upgrading energy to level {next_energy_level} | "
                                    f"Price: {next_energy_price} | "
                                    f"Max: {settings.MAX_ENERGY_LEVEL}"
                                )
                                if await self.level_up(boost_id=3):
                                    logger.success(f"{self.session_name} | ✅ Energy upgraded to level {next_energy_level}")
                                    upgraded = True

                            if balance >= next_charge_price and next_charge_level <= settings.MAX_CHARGE_LEVEL:
                                logger.info(
                                    f"{self.session_name} | "
                                    f"⏱ Upgrading recovery to level {next_charge_level} | "
                                    f"Price: {next_charge_price} | "
                                    f"Max: {settings.MAX_CHARGE_LEVEL}"
                                )
                                if await self.level_up(boost_id=2):
                                    logger.success(f"{self.session_name} | ✅ Recovery upgraded to level {next_charge_level}")
                                    upgraded = True

                            if upgraded:
                                await asyncio.sleep(1)
                                continue

                        except TooManyRequestsError:
                            sleep_time = randint(settings.SLEEP_BY_MIN_ENERGY[0], settings.SLEEP_BY_MIN_ENERGY[1])
                            logger.warning(
                                f"{self.session_name} | "
                                f"⚠️ Too many requests while upgrading | "
                                f"⏳ Waiting {format_duration(sleep_time)}"
                            )
                            await asyncio.sleep(sleep_time)
                            continue

                        sleep_time = randint(settings.SLEEP_BY_MIN_ENERGY[0], settings.SLEEP_BY_MIN_ENERGY[1])
                        logger.info(
                            f"{self.session_name} | "
                            f"⚡️ Energy is out | "
                            f"💰 Balance: {balance} | "
                            f"⏳ Waiting {format_duration(sleep_time)}"
                        )
                        await asyncio.sleep(sleep_time)

                except TooManyRequestsError:
                    sleep_time = randint(settings.SLEEP_BY_MIN_ENERGY[0], settings.SLEEP_BY_MIN_ENERGY[1])
                    logger.warning(
                        f"{self.session_name} | "
                        f"⚠️ Too many requests | "
                        f"⏭ Waiting {format_duration(sleep_time)}"
                    )
                    await asyncio.sleep(sleep_time)
                    continue

            except InvalidSession as error:
                raise error

            except Exception as error:
                logger.error(f"{self.session_name} | ❌ Unknown error: {error}")
                await asyncio.sleep(3)

    async def login(self, tg_web_data: str) -> str:
        try:
            request_data = {"code": tg_web_data}

            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/user/login',
                json=request_data,
                headers=HEADERS
            )

            if not response:
                raise InvalidSession("Authorization error: no response from server")

            if response.get('code') != 0:
                raise InvalidSession(f"Authorization error: {response.get('message', 'unknown error')}")

            if 'data' not in response or 'token' not in response['data']:
                raise InvalidSession("Authorization error: invalid response format")

            token = response['data']['token']
            self._access_token = token
            logger.success(f"{self.session_name} | Successful authorization")
            return token

        except Exception as error:
            logger.error(f"{self.session_name} | Authorization error: {error}")
            await asyncio.sleep(3)
            raise InvalidSession("Authorization error")

    async def get_profile_data(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/account/getAccountInfo'
            )
            return response['data'] if response else {}
        except TooManyRequestsError:
            raise
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving profile data: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_game_data(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/game/getGameInfo'
            )
            return response['data'] if response else {}
        except TooManyRequestsError:
            raise
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving game data: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_boosts_info(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/build/getAccountBuildInfo'
            )
            return response['data'] if response else {}
        except TooManyRequestsError:
            raise
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving boost information: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_special_box_info(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/game/getSpecialBoxInfo'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving special box information: {error}")
            await asyncio.sleep(3)
            return {}

    async def level_up(self, boost_id: int) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/build/levelUp',
                json=str(boost_id)
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error leveling up boost {boost_id}: {error}")
            await asyncio.sleep(3)
            return False

    async def apply_turbo_boost(self) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/game/recoverSpecialBox'
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error applying turbo boost: {error}")
            await asyncio.sleep(3)
            return False

    async def apply_energy_boost(self) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/game/recoverCoinPool'
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error applying energy boost: {error}")
            await asyncio.sleep(3)
            return False

    async def send_taps(self, taps: int) -> Tuple[bool, Dict[str, Any]]:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/game/collectCoin',
                json=str(taps)
            )
            if not response or not response['data']:
                return False, {}

            data = response['data']
            return data['collectStatus'], {
                'collect_amount': data['collectAmount'],
                'current_amount': data['currentAmount'],
                'total_amount': data['totalAmount']
            }
        except Exception as error:
            logger.error(f"{self.session_name} | Error sending taps: {error}")
            await asyncio.sleep(3)
            return False, {}

    async def send_taps_with_turbo(self) -> Tuple[bool, Dict[str, Any]]:
        try:
            special_box_info = await self.get_special_box_info()
            box_type = special_box_info['recoveryBox']['boxType']
            taps = special_box_info['recoveryBox']['specialBoxTotalCount']

            await asyncio.sleep(10)

            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/game/collectSpecialBoxCoin',
                json={'boxType': box_type, 'coinCount': taps}
            )
            if not response or not response['data']:
                return False, {}

            data = response['data']
            return data['collectStatus'], {
                'collect_amount': data['collectAmount'],
                'current_amount': data.get('currentAmount'),
                'total_amount': data.get('totalAmount')
            }
        except Exception as error:
            logger.error(f"{self.session_name} | Error sending taps with turbo: {error}")
            await asyncio.sleep(3)
            return False, {}

    async def get_user_info(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/user/info'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving user information: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_user_active_level(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/activity/userActiveLevel'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving active level: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_stop_bonus(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/account/getUserStopBonus'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving bonus information: {error}")
            await asyncio.sleep(3)
            return {}

    async def claim_stop_bonus(self) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/account/claimStopBonus'
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error claiming bonus: {error}")
            await asyncio.sleep(3)
            return False

    async def join_activity(self, activity_code: str) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/activity/doJoinActivity',
                json={"activityCode": activity_code}
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error joining activity: {error}")
            await asyncio.sleep(3)
            return False

    async def get_activity_status(self, activity_code: str) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url=f'https://bi.yescoin.gold/activity/getJoinActivityStatus?activityCode={activity_code}'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving activity status: {error}")
            await asyncio.sleep(3)
            return {}

    async def check_proxy(self, proxy: Proxy) -> None:
        try:
            response = await self.make_request(
                method='GET',
                url='https://httpbin.org/ip',
                timeout=aiohttp.ClientTimeout(5)
            )
            if response:
                ip = response.get('origin')
                logger.info(f"{self.session_name} | Proxy IP: {ip}")
        except Exception as error:
            logger.error(f"{self.session_name} | Proxy: {proxy} | Error: {error}")

    async def get_task_list(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/task/getTaskList'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving task list: {error}")
            await asyncio.sleep(3)
            return {}

    async def click_task(self, task_id: str) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/task/clickTask',
                json=task_id
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error clicking task {task_id}: {error}")
            await asyncio.sleep(3)
            return False

    async def check_task(self, task_id: str) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/task/checkTask',
                json=task_id
            )
            return bool(response and response['data'])
        except Exception as error:
            logger.error(f"{self.session_name} | Error checking task {task_id}: {error}")
            await asyncio.sleep(3)
            return False

    async def claim_task_reward(self, task_id: str) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/task/claimTaskReward',
                json=task_id
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error claiming reward for task {task_id}: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_task_bonus_info(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/task/getFinishTaskBonusInfo'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving bonus information: {error}")
            await asyncio.sleep(3)
            return {}

    async def process_tasks(self) -> None:
        try:
            task_list = await self.get_task_list()
            if not task_list:
                return

            for task in task_list.get('taskList', []):
                task_id = task['taskId']
                task_name = task['taskName']
                task_bonus = task['taskBonusAmount']
                task_status = task['taskStatus']

                if task_status == 0:
                    logger.info(
                        f"{self.session_name} | "
                        f"Executing task: {task_name} | "
                        f"Reward: {task_bonus}"
                    )

                    if await self.click_task(task_id):
                        await asyncio.sleep(5)
                        if await self.check_task(task_id):
                            reward_data = await self.claim_task_reward(task_id)
                            if reward_data:
                                logger.success(
                                    f"{self.session_name} | "
                                    f"Task {task_name} completed | "
                                    f"Received: {reward_data.get('bonusAmount', 0)}"
                                )

            for task in task_list.get('specialTaskList', []):
                task_id = task['taskId']
                task_name = task['taskName']
                task_bonus = task['taskBonusAmount']
                task_status = task['taskStatus']

                if task_status == 0:
                    logger.info(
                        f"{self.session_name} | "
                        f"Executing special task: {task_name} | "
                        f"Reward: {task_bonus}"
                    )

                    if await self.click_task(task_id):
                        await asyncio.sleep(5)
                        if await self.check_task(task_id):
                            reward_data = await self.claim_task_reward(task_id)
                            if reward_data:
                                logger.success(
                                    f"{self.session_name} | "
                                    f"Special task {task_name} completed | "
                                    f"Received: {reward_data.get('bonusAmount', 0)}"
                                )

            bonus_info = await self.get_task_bonus_info()
            if bonus_info:
                logger.info(
                    f"{self.session_name} | "
                    f"Daily tasks: {bonus_info.get('dailyTaskFinishCount', 0)}/{bonus_info.get('dailyTaskTotalCount', 0)} | "
                    f"Regular tasks: {bonus_info.get('commonTaskFinishCount', 0)}/{bonus_info.get('commonTaskTotalCount', 0)}"
                )

        except Exception as error:
            logger.error(f"{self.session_name} | Error processing tasks: {error}")
            await asyncio.sleep(3)

    async def get_daily_missions(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/mission/getDailyMission'
            )
            return response['data'] if response else []
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving daily missions: {error}")
            await asyncio.sleep(3)
            return []

    async def process_daily_mission(self, mission_id: int) -> bool:
        try:
            for attempt in range(2):
                if attempt > 0:
                    logger.info(f"{self.session_name} | 🔄 Retrying mission {mission_id}")
                    await asyncio.sleep(5)

                click_response = await self.make_request(
                    method='POST',
                    url='https://bi.yescoin.gold/mission/clickDailyMission',
                    json=mission_id
                )
                if not click_response or not click_response['data']:
                    logger.error(f"{self.session_name} | ❌ Mission {mission_id} | Attempt {attempt + 1} | Error clicking")
                    continue

                await asyncio.sleep(7)

                check_response = await self.make_request(
                    method='POST',
                    url='https://bi.yescoin.gold/mission/checkDailyMission',
                    json=mission_id
                )
                if not check_response or not check_response['data']:
                    logger.error(f"{self.session_name} | ❌ Mission {mission_id} | Attempt {attempt + 1} | Error checking")
                    continue

                await asyncio.sleep(5)

                claim_response = await self.make_request(
                    method='POST',
                    url='https://bi.yescoin.gold/mission/claimReward',
                    json=mission_id
                )
                if not claim_response or not claim_response.get('data'):
                    logger.error(f"{self.session_name} | ❌ Mission {mission_id} | Attempt {attempt + 1} | Error claiming reward")
                    continue

                reward_data = claim_response['data']
                logger.success(
                    f"{self.session_name} | ✅ Mission completed | "
                    f"💰 Reward: {reward_data.get('reward', 0)} | "
                    f"⭐️ Points: {reward_data.get('score', 0)} | "
                    f"🔄 Attempt: {attempt + 1}"
                )
                return True

            logger.warning(f"{self.session_name} | ⚠️ Mission {mission_id} not completed after two attempts")
            return False

        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error processing mission {mission_id}: {error}")
            await asyncio.sleep(3)
            return False

    async def process_daily_missions(self) -> None:
        try:
            missions = await self.get_daily_missions()
            if not missions:
                logger.warning(f"{self.session_name} | ⏭️ Skipping missions")
                return

            logger.info(f"{self.session_name} | 📋 Starting daily missions...")

            completed = 0
            failed = 0
            total = len(missions)

            for mission in missions:
                mission_id = mission['missionId']
                mission_name = mission['name']
                mission_status = mission['missionStatus']
                mission_reward = mission['reward']

                if mission_status == 0:
                    logger.info(
                        f"{self.session_name} | "
                        f"🎯 Mission: {mission_name} | "
                        f"💰 Reward: {mission_reward}"
                    )
                    try:
                        if await self.process_daily_mission(mission_id):
                            completed += 1
                        else:
                            failed += 1
                        await asyncio.sleep(5)
                    except Exception as e:
                        logger.warning(f"{self.session_name} | ⏭️ Skipping mission {mission_name}: {str(e)}")
                        failed += 1
                else:
                    completed += 1

            logger.info(
                f"{self.session_name} | "
                f"📊 Mission results: ✅ {completed}/{total} completed"
                + (f" | ❌ {failed} failed" if failed > 0 else "")
            )

        except Exception as error:
            logger.warning(f"{self.session_name} | ⏭️ Skipping missions: {error}")
            await asyncio.sleep(3)

    async def get_squad_info(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/squad/mySquad'
            )
            return response['data'] if response else {}
        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error retrieving squad information: {error}")
            await asyncio.sleep(3)
            return {}

    async def get_recommended_squads(self) -> List[Dict[str, Any]]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/squad/getRecommendSquadList'
            )
            return response['data']['list'] if response and 'data' in response else []
        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error retrieving squad list: {error}")
            await asyncio.sleep(3)
            return []

    async def join_squad(self, squad_id: str, squad_tg_link: str) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/squad/joinSquad',
                json={
                    "squadId": squad_id,
                    "squadTgLink": squad_tg_link
                }
            )
            if response and response.get('code') == 0:
                squad_info = response['data']['squadInfo']
                logger.success(
                    f"{self.session_name} | "
                    f"✅ Joined squad {squad_info['squadTitle']} | "
                    f"👥 Members: {squad_info['squadMembers']} | "
                    f"📊 Level: {squad_info['squadLevel']}"
                )
                return True
            return False
        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error joining squad: {error}")
            await asyncio.sleep(3)
            return False

    async def process_squad(self) -> None:
        try:
            squad_info = await self.get_squad_info()
            if squad_info.get('isJoinSquad'):
                return

            logger.info(f"{self.session_name} | 🔍 Searching for a squad to join...")
            squads = await self.get_recommended_squads()
            if not squads:
                return

            for squad in squads[:3]:
                squad_id = squad['squadIdStr']
                squad_tg_link = squad['squadTgLink']
                squad_title = squad['squadTitle']
                squad_members = squad['squadMembers']

                logger.info(
                    f"{self.session_name} | "
                    f"🎯 Trying to join squad {squad_title} | "
                    f"👥 Members: {squad_members}"
                )

                if await self.join_squad(squad_id, squad_tg_link):
                    break

                await asyncio.sleep(2)

        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error processing squad: {error}")
            await asyncio.sleep(3)

    async def get_offline_bonus_info(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/game/getOfflineYesPacBonusInfo'
            )
            return response['data'] if response else []
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving offline bonus info: {error}")
            await asyncio.sleep(3)
            return []

    async def claim_offline_bonus(self, transaction_id: str, claim_type: int, create_at: int) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/game/claimOfflineBonus',
                json={
                    "id": transaction_id,
                    "createAt": create_at,
                    "claimType": claim_type,
                    "destination": ""
                }
            )
            if response and response.get('code') == 0:
                bonus_data = response['data']
                logger.success(
                    f"{self.session_name} | "
                    f"✅ Claimed offline bonus | "
                    f"💰 Amount: {bonus_data['collectAmount']} | "
                    f"📊 Extra: {bonus_data['extraPercentage']}%"
                )
                return True
            return False
        except Exception as error:
            logger.error(f"{self.session_name} | Error claiming offline bonus: {error}")
            await asyncio.sleep(3)
            return False

    async def get_signin_list(self) -> Dict[str, Any]:
        try:
            response = await self.make_request(
                method='GET',
                url='https://bi.yescoin.gold/signIn/list'
            )
            return response['data'] if response else []
        except Exception as error:
            logger.error(f"{self.session_name} | Error retrieving signin list: {error}")
            await asyncio.sleep(3)
            return []

    async def claim_signin(self, signin_id: str, create_at: int, signin_type: int = 1) -> bool:
        try:
            response = await self.make_request(
                method='POST',
                url='https://bi.yescoin.gold/signIn/claim',
                json={
                    "id": signin_id,
                    "createAt": create_at,
                    "signInType": signin_type,
                    "destination": ""
                }
            )
            if response and response.get('code') == 0:
                reward_data = response['data']
                logger.success(
                    f"{self.session_name} | "
                    f"✅ Daily check-in completed | "
                    f"💰 Reward: {reward_data['reward']}"
                )
                return True
            return False
        except Exception as error:
            logger.error(f"{self.session_name} | Error claiming signin reward: {error}")
            await asyncio.sleep(3)
            return False

    async def process_offline_bonus(self) -> None:
        try:
            bonus_info = await self.get_offline_bonus_info()
            if not bonus_info:
                return

            for bonus in bonus_info:
                if bonus['collectStatus']:
                    create_at = int(time())
                    await self.claim_offline_bonus(
                        transaction_id=bonus['transactionId'],
                        claim_type=bonus['claimType'],
                        create_at=create_at
                    )
                    await asyncio.sleep(2)

        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error processing offline bonus: {error}")
            await asyncio.sleep(3)

    async def process_signin(self) -> None:
        try:
            signin_list = await self.get_signin_list()
            if not signin_list:
                return

            for signin in signin_list:
                if signin['status'] == 1 and signin['checkIn'] == 0:
                    create_at = int(time())
                    await self.claim_signin(
                        signin_id=signin['id'],
                        create_at=create_at
                    )
                    break  # Только один чекин в день

        except Exception as error:
            logger.error(f"{self.session_name} | ❌ Error processing signin: {error}")
            await asyncio.sleep(3)


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = BaseBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(f"Invalid Session: {e}")
