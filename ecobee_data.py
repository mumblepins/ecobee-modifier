import logging
import math
import shelve
import sys
from datetime import datetime, timedelta
from threading import Event

import pyecobee as peb
import pytz

logger = logging.getLogger(__name__)
from utils import wait


class EcobeeData:
    _ecobee_service: peb.EcobeeService = None
    _authorize_response: peb.EcobeeAuthorizeResponse = None
    _authorize_expires: datetime = None
    _shelf_filename: str = None
    _backlight_settings: peb.Settings = None
    _got_token = False
    _exit_event: Event = None

    _backlight_on = peb.Settings(backlight_off_during_sleep=False,
                                 backlight_off_time=20,
                                 backlight_sleep_intensity=1,
                                 backlight_on_intensity=9
                                 )

    _backlight_off = peb.Settings(backlight_off_during_sleep=True,
                                  backlight_off_time=0,
                                  backlight_sleep_intensity=0,
                                  backlight_on_intensity=0
                                  )

    def __init__(self, shelf_filename, thermostat_name, ecobee_api_key, exit_event):
        self._exit_event = exit_event
        self._shelf_filename = shelf_filename
        pyecobee_db: shelve.DbfilenameShelf = None
        try:
            pyecobee_db = shelve.open(shelf_filename, protocol=2)
            data: EcobeeData = pyecobee_db[thermostat_name]
            self.__setstate__(data.__getstate__())
        except KeyError:
            # application_key = input('Please enter the API key of your ecobee App: ')
            self._ecobee_service = peb.EcobeeService(thermostat_name=thermostat_name, application_key=ecobee_api_key)
        finally:
            pyecobee_db.close()

    def __getstate__(self):
        return (self._ecobee_service,
                self._authorize_response,
                self._authorize_expires,
                self._backlight_settings,
                self._got_token)

    def __setstate__(self, state):
        self._ecobee_service, self._authorize_response, self._authorize_expires, self._backlight_settings, self._got_token = state

    # <editor-fold desc="Properties">
    @property
    def got_token(self):
        return self._got_token

    @property
    def ecobee_service(self):
        return self._ecobee_service

    @ecobee_service.setter
    def ecobee_service(self, value):
        self._ecobee_service = value

    @property
    def authorize_response(self):
        return self._authorize_response

    @authorize_response.setter
    def authorize_response(self, value):
        self._authorize_response = value

    @property
    def authorize_expires(self):
        return self._authorize_expires

    @authorize_expires.setter
    def authorize_expires(self, value):
        self._authorize_expires = value

    @property
    def backlight_settings(self):
        return self._backlight_settings

    @backlight_settings.setter
    def backlight_settings(self, value):
        self._backlight_settings = value

    @property
    def sensors(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_sensors=True)
        )
        sensors = thermostat_response.thermostat_list[0].remote_sensors
        return sensors

    @property
    def sensor_temps(self):
        temps = {}
        for sensor in self.sensors:
            sensor_name = sensor.name
            caps = [s for s in sensor.capability if s.type == 'temperature']
            if len(caps) > 0:
                temps[sensor_name] = float(caps[0].value) / 10.0

        return temps

    # </editor-fold>

    def persist_to_shelf(self):
        shelf = shelve.open(self._shelf_filename, protocol=2)
        shelf[self.ecobee_service.thermostat_name] = self
        shelf.close()

    def refresh_tokens(self):
        response = self.ecobee_service.refresh_tokens()
        logger.debug('TokenResponse returned from ecobee_service.refresh_tokens():\n{0}'.format(
            response.pretty_format()))
        self.persist_to_shelf()

    def authorize(self):
        self.authorize_response = self.ecobee_service.authorize()
        logger.debug('AutorizeResponse returned from ecobee_service.authorize():\n{0}'.format(
            self.authorize_response.pretty_format()))
        self.authorize_expires = datetime.utcnow() + \
                                 timedelta(minutes=self.authorize_response.expires_in)
        sys.stdout.flush()
        sys.stderr.flush()
        logger.info('Please goto ecobee.com, login to the web portal and click on the settings tab. Ensure the My '
                    'Apps widget is enabled. If it is not click on the My Apps option in the menu on the left. In the '
                    'My Apps widget paste "{0}" and in the textbox labelled "Enter your 4 digit pin to '
                    'install your third party app" and then click "Install App". The next screen will display any '
                    'permissions the app requires and will ask you to click "Authorize" to add the application.\n\n'.format(
            self.authorize_response.ecobee_pin))
        self.persist_to_shelf()
        wait(self.authorize_response.interval, self._exit_event, interval=5,
             extra_message=" waiting, please enter '{}'...".format(self.authorize_response.ecobee_pin))

    def wait_for_token(self):
        while datetime.utcnow() < self.authorize_expires and not self._exit_event.is_set():
            try:
                token_response = self.ecobee_service.request_tokens()
                logger.debug("Got token:\n%s", token_response.pretty_format())
                self.persist_to_shelf()
                break
            except peb.EcobeeAuthorizationException as e:
                if "authorization_pending" in e.error:
                    wait(self.authorize_response.interval, self._exit_event,
                         extra_message=" waiting, please enter '{}'...".format(self.authorize_response.ecobee_pin))
                else:
                    raise e
        else:
            logger.info("Authorization token expired, trying again")
            self.authorize()
            self.wait_for_token()

    def get_token(self, fail_fast=False):
        if not self.ecobee_service.authorization_token:
            if fail_fast: return False
            self.authorize()
        if not self.ecobee_service.access_token:
            if fail_fast: return False
            self.wait_for_token()

        now_utc = datetime.now(pytz.utc)
        if now_utc > self.ecobee_service.refresh_token_expires_on:
            if fail_fast: return False
            self.authorize()
            self.wait_for_token()
        elif now_utc > self.ecobee_service.access_token_expires_on:
            self.refresh_tokens()

        self._got_token = True
        return True

    def set_humidity(self, rh):
        thermostat_response = self.ecobee_service.update_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match=''),
            thermostat=peb.Thermostat(
                settings=peb.Settings(
                    humidity=int(rh)
                )
            )
        )
        logger.debug(thermostat_response.pretty_format())

    def set_humidity_auto(self):
        self.set_humidity_mode('auto')

    def set_humidity_mode(self, mode):
        thermostat_response = self.ecobee_service.update_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match=''),
            thermostat=peb.Thermostat(
                settings=peb.Settings(
                    humidifier_mode=mode)
            )
        )
        logger.debug(thermostat_response.pretty_format())

    def get_humidity_mode(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_settings=True)
        )
        return thermostat_response.thermostat_list[0].settings.humidifier_mode

    def set_fan_min_on_time(self, min_on_time):
        thermostat_response = self.ecobee_service.update_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match=''),
            thermostat=peb.Thermostat(
                settings=peb.Settings(
                    fan_min_on_time=int(min_on_time),
                )
            )
        )
        logger.debug(thermostat_response.pretty_format())

    def store_backlight_settings(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_settings=True)
        )

        bl_settings: peb.Settings = thermostat_response.thermostat_list[0].settings
        new_bl_settings = peb.Settings()
        backlight_keys = [k for k in new_bl_settings.attribute_name_map.keys() if 'backlight' in k and k.lower() == k]
        different = False
        for k in backlight_keys:
            set = getattr(bl_settings, k)
            if set != getattr(self._backlight_off, k):
                different = True
            setattr(new_bl_settings, k, set)
        if not different:
            logger.debug('not saving, backlight already off')
        else:
            logger.debug('saving backlight settings')
            self.backlight_settings = new_bl_settings
            self.persist_to_shelf()

    def turn_backlight_off(self):
        logger.debug("Turning Backlight Off")
        thermostat_response = self.ecobee_service.update_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match=''),
            thermostat=peb.Thermostat(
                settings=self._backlight_off
            )
        )
        logger.debug(thermostat_response.pretty_format())

    def turn_backlight_on(self):
        logger.debug("Turning Backlight On")
        thermostat_response = self.ecobee_service.update_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match=''),
            thermostat=peb.Thermostat(
                settings=self.backlight_settings
            )
        )
        logger.debug(thermostat_response.pretty_format())

    def get_cur_inside_temp(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_runtime=True)
        )
        inside_temp = thermostat_response.thermostat_list[0].runtime.actual_temperature / 10.0
        des_inside_temp = thermostat_response.thermostat_list[0].runtime.desired_heat / 10.0
        return float(inside_temp), float(des_inside_temp)

    def get_cur_inside_humidity(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_runtime=True)
        )
        humidity = thermostat_response.thermostat_list[0].runtime.actual_humidity
        return float(humidity)

    def get_cur_hvac_mode(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_equipment_status=True)
        )
        return thermostat_response.thermostat_list[0].equipment_status

    def get_fan_min_on_time(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                          include_settings=True)
        )
        return thermostat_response.thermostat_list[0].settings.fan_min_on_time

    def occupied(self):
        thermostat_response = self.ecobee_service.request_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                                    include_sensors=True, include_program=True, include_events=True)
        )
        if thermostat_response.thermostat_list[0].program.current_climate_ref in ['home', 'sleep']:
            return True

        for sensor in thermostat_response.thermostat_list[0].remote_sensors:
            caps = [a.value == 'true' for a in sensor.capability if a.type == 'occupancy']
            if any(caps):
                return True
        for event in thermostat_response.thermostat_list[0].events:
            if event.running and (event.heat_hold_temp > 640 or event.cool_hold_temp < 760):
                return True
        return False

    def get_future_set_temp(self):

        thermostat_response = self.ecobee_service.request_thermostats(
            selection=peb.Selection(selection_type=peb.SelectionType.REGISTERED.value, selection_match='',
                                    include_program=True, include_events=True)
        )
        thermostat = thermostat_response.thermostat_list[0]
        therm_time = datetime.strptime(thermostat.thermostat_time, '%Y-%m-%d %H:%M:%S')
        future_time = therm_time + timedelta(hours=1)
        day_of_week = future_time.weekday()
        time_of_day = math.floor((future_time.hour * 60 + future_time.minute) / 30)
        future_climate = thermostat.program.schedule[day_of_week][time_of_day]
        future_temp = [c.heat_temp / 10.0
                       for c in thermostat.program.climates
                       if c.climate_ref == future_climate
                       ][0]
        logger.debug('future temp based on schedule: %s', future_temp)
        current_event = [e for e in thermostat.events if e.running]
        if current_event:
            ce = current_event[0]
            end_event = datetime.strptime('{} {}'.format(ce.end_date, ce.end_time), '%Y-%m-%d %H:%M:%S')
            if end_event > future_time:
                future_temp = ce.heat_hold_temp / 10.0
                logger.debug('Using Override Temp: %s', future_temp)
        return future_temp
        # logger.debug(thermostat_response.thermostat_list[0]
        #              .pretty_format())

    def graceful_shutdown(self):
        if self.got_token:
            self.turn_backlight_on()
            self.set_fan_min_on_time(20)
            self.set_humidity_auto()
        self.persist_to_shelf()
