import json
import logging
import math
import os
import signal
import sys
from datetime import datetime, timedelta
from threading import Event

import pyowm
import pytz
from pyowm.weatherapi25.forecaster import Forecaster
from pyowm.weatherapi25.observation import Observation

from ecobee_data import EcobeeData
from utils import wait, string_to_bool

log_handler = logging.StreamHandler(sys.stderr)
log_handler.flush = sys.stderr.flush

logging.basicConfig(format='%(asctime)s,%(msecs)d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
                    datefmt='%d-%m-%Y:%H:%M:%S',
                    level=logging.DEBUG, handlers=[log_handler])
logger = logging.getLogger(__name__)
logging.getLogger('requests').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('pyecobee').setLevel(logging.CRITICAL)
logging.getLogger('ecobee_data').setLevel(logging.DEBUG)

TEMP_DELTA = 20
R_VALUE=2.5
polling_interval = 30

ecobee: EcobeeData = None
ecobee_api_key: str = None
owm_api_key: str = None

shelf_name = 'pyecobee.shelf'
thermostat_name = 'Home'

exit_signal = Event()


def signal_handler(sig, frame):
    global ecobee
    logging.warning("Got signal %s, exiting", signal.Signals(sig).name)

    if ecobee is None and ecobee_api_key is not None:
        ecobee = EcobeeData(shelf_name, thermostat_name, ecobee_api_key, exit_signal)
        ecobee.get_token(True)
    logging.warning("Persisting to shelf")
    ecobee.graceful_shutdown()
    logging.warning("Exiting...")
    exit()


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def calc_relative_humidity(temp, dewpoint):
    temp = (temp - 32) * 5 / 9
    dewpoint = (dewpoint - 32) * 5 / 9
    rh = 100 * (math.exp((17.625 * dewpoint) / (243.04 + dewpoint)) / math.exp((17.625 * temp) / (243.04 + temp)))
    return rh


def desired_humid_perc(inside_temp, outside_temp, r_value: float = 2.5,
                       # diff: float = 20
                       ):
    des_dewpoint = (inside_temp - outside_temp) / (.17 + .68 + r_value) * (r_value + .17) + outside_temp
    # des_dewpoint = outside_temp + diff
    return calc_relative_humidity(inside_temp, des_dewpoint)


def adjust_fan_min(ecobee):
    occupied = ecobee.occupied()
    current_fan = ecobee.get_fan_min_on_time()
    occupied_fan = int(os.environ.get('FAN_OCCUPIED_TIME', 20))
    away_fan = int(os.environ.get('FAN_AWAY_TIME', 5))
    if occupied:
        if current_fan != occupied_fan:
            logger.info("Changing fan to occupied: %d", occupied_fan)
            ecobee.set_fan_min_on_time(occupied_fan)
        else:
            logger.info("no need to change fan min runtime")
    else:
        if current_fan != away_fan:
            logger.info("Changing fan to away: %d", away_fan)
            ecobee.set_fan_min_on_time(away_fan)
        else:
            logger.info("no need to change fan min runtime")


def switch_backlight():
    if string_to_bool(os.environ.get('SWITCH_BACKLIGHT', 'true')):
        # if str(os.environ.get('SWITCH_BACKLIGHT', 1)).lower() not in ['0', 'false', 'f']:
        if ecobee.occupied():
            ecobee.turn_backlight_on()
        else:
            ecobee.turn_backlight_off()


def switch_humidifier():
    cur_humid = ecobee.get_cur_inside_humidity()
    cur_humid_mode = ecobee.get_humidity_mode()
    cur_hvac_mode = ecobee.get_cur_hvac_mode()
    if 'auxHeat' in cur_hvac_mode:
        if cur_humid_mode != 'manual':
            logger.debug('heat on, setting humidifier to manual')
            ecobee.set_humidity_mode('manual')
        else:
            logger.debug('heat on, already set to manual, doing nothing')
        return

    if cur_humid_mode == 'manual' and \
            cur_humid >= max_steam_humidity + steam_humidity_hysteresis:
        logger.debug('heat not on and humidity (%0.0f%%) above (%0.0f%%), turning off humidifier', cur_humid,
                     max_steam_humidity + steam_humidity_hysteresis)
        ecobee.set_humidity_mode('off')
    elif cur_humid_mode != 'manual' and \
            cur_humid <= max_steam_humidity:
        logger.debug('heat not on and humidity (%0.0f%%) below (%0.0f%%), turning on humidifier', cur_humid,
                     max_steam_humidity)
        ecobee.set_humidity_mode('manual')
    else:
        logger.debug('heat not on and humidifier mode is "%s" with humidity of %0.0f%%, not changing anything',
                     cur_humid_mode, cur_humid)

    # if 'auxHeat' not in ecobee.get_cur_hvac_mode() and \
    #         'manual' == ecobee.get_humidity_mode() and \
    #         cur_humid > max_steam_humidity:
    #     logger.debug('heat not on and humidity (%0.0f%%) above (%0.0f%%), turning off humidifier', cur_humid,
    #                  max_steam_humidity)
    #     ecobee.set_humidity_mode('off')
    # else:
    #     logger.debug('setting humidifier to manual')
    #     ecobee.set_humidity_mode('manual')


def get_fan_runtime():
    temps = ecobee.sensor_temps
    sensor_delta = max(temps.values()) - min(temps.values())
    # sensor_delta = 4

    fan_max = json.loads(os.environ.get('FAN_MAX', '[8,60]'))
    fan_min = json.loads(os.environ.get('FAN_MIN', '[1,5]'))
    if sensor_delta > fan_max[0]:
        runtime = fan_max[1]
    elif sensor_delta < fan_min[0]:
        runtime = fan_min[1]
    else:
        runtime = fan_factors[0] * pow(sensor_delta, 5) + \
                  fan_factors[1] * pow(sensor_delta, 4) + \
                  fan_factors[2] * pow(sensor_delta, 3) + \
                  fan_factors[3] * pow(sensor_delta, 2) + \
                  fan_factors[4] * sensor_delta + \
                  fan_factors[5]
    rt_rounded = min(max(int(5 * round(runtime / 5)), fan_min[1]), fan_max[1])
    logger.debug("Fan runtime setting %d (%0.3f) for ΔT=%0.1f", rt_rounded, runtime, sensor_delta)
    return rt_rounded


def run():
    global ecobee
    ecobee = EcobeeData(shelf_name, thermostat_name, ecobee_api_key, exit_signal)
    ecobee.get_token()
    # ecobee.get_humidity_mode()
    # return
    ecobee.store_backlight_settings()
    fan_mode = os.environ.get('FAN_MODE', 'DELTA').lower()
    if fan_mode[:3] == 'del':
        fantime = get_fan_runtime()
        logger.info('Setting min fan runtime to %d', fantime)
        ecobee.set_fan_min_on_time(fantime)
    elif fan_mode[:3] == 'occ':
        adjust_fan_min(ecobee)
    switch_backlight()

    in_temp, des_in_temp = ecobee.get_cur_inside_temp()
    outside_temp, future_out_temp = get_owm_outside_temps()
    future_des_temp = ecobee.get_future_set_temp()
    cur_out_cur_in_rh = desired_humid_perc(in_temp, outside_temp, r_value)
    logger.info("RH Based on current inside (%0.1f F) and outside (%0.1f F) temp: %0.1f%%",
                in_temp, outside_temp,
                cur_out_cur_in_rh)
    future_out_future_in_rh = desired_humid_perc(future_des_temp, future_out_temp, r_value)
    logger.info("RH Based on desired inside (%0.1f F) and future outside (%0.1f F) temp: %0.1f%%",
                future_des_temp, future_out_temp, future_out_future_in_rh)

    rh_set = max(min(max_humidity,
                     cur_out_cur_in_rh,
                     future_out_future_in_rh,
                     ), min_humidity)
    logger.info("RH unrounded: %0.1f%%", rh_set)

    rh_set = round(rh_set / 2) * 2
    logger.info("actual humidity setting %0.1f%%", rh_set)
    ecobee.set_humidity(round(rh_set))
    switch_humidifier()
    ecobee = None


def get_owm_outside_temps():
    cur_weather: Observation
    cur_forecast: Forecaster
    owm = pyowm.OWM(owm_api_key)
    location_lat = os.environ.get('OWM_LATITUDE', None)
    location_lon = os.environ.get('OWM_LONGITUDE', None)
    location_id = os.environ.get('OWM_ID', None)
    location_name = os.environ.get('OWM_LOCATION', None)
    if location_lon and location_lat:
        location_lon = float(location_lon)
        location_lat = float(location_lat)
        cur_weather = owm.weather_at_coords(location_lat, location_lon)
        cur_forecast = owm.three_hours_forecast_at_coords(location_lat, location_lon)
    elif location_id:
        location_id = int(location_id)
        cur_weather = owm.weather_at_id(location_id)
        cur_forecast = owm.three_hours_forecast_at_id(location_id)
    elif location_name:
        cur_weather = owm.weather_at_place(location_name)
        cur_forecast = owm.three_hours_forecast(location_name)
    else:
        raise ValueError('One OWM location type needs to be specified (lat-lon,id,or string)')
    cur_outside_temp = cur_weather.get_weather().get_temperature(unit='fahrenheit')
    future_time = datetime.now(pytz.utc) + timedelta(hours=1)
    mintime = 60 * 60 * 2  # 2 hours
    future_outside_temp = None
    for f in cur_forecast.get_forecast():
        timediff = abs((f.get_reference_time('date') - future_time).total_seconds())

        if timediff < mintime:
            mintime = timediff
            future_outside_temp = f.get_temperature(unit='fahrenheit')
            logger.debug("OWM: %s, %0.1f -> %0.1f", f.get_reference_time('date'), mintime, future_outside_temp['temp'])
    return cur_outside_temp['temp'], future_outside_temp['temp']


if __name__ == '__main__':
    thermostat_name = 'My Thermostat'
    ecobee_api_key = os.environ['ECOBEE_API_KEY']
    owm_api_key = os.environ['OWM_API_KEY']
    temp_delta = float(os.environ.get('DEWPOINT_DELTA', TEMP_DELTA))
    r_value=float(os.environ.get('R_VALUE', R_VALUE))
    update_interval = int(os.environ.get('UPDATE_INTERVAL', 600))
    max_steam_humidity = float(os.environ.get('MAX_STEAM_HUMIDITY', 40))
    steam_humidity_hysteresis = float(os.environ.get('STEAM_HUMIDITY_HYST', 2))

    max_humidity = float(os.environ.get('MAX_HUMIDITY', 50))
    min_humidity = float(os.environ.get('MIN_HUMIDITY', 10))
    loglevel = os.environ.get('LOG_LEVEL', "INFO")
    numeric_level = getattr(logging, loglevel.upper(), 20)
    logger.setLevel(numeric_level)
    logger.warning("Logging set to %s", logging.getLevelName(numeric_level))
    fan_factors = json.loads(os.environ.get(
        'FAN_FACTORS',
        '[0.43651,-5.99206,29.9206,-19.3651]'
    ))

    fan_factors = [0] * (6 - len(fan_factors)) + fan_factors
    logger.debug("Fan factors are %s", fan_factors)

    while not exit_signal.is_set():
        run()
        log_handler.flush()
        # break
        show_interval = max(10, update_interval / 10.0)
        wait(update_interval, exit_signal, interval=show_interval,
             extra_message='/{} seconds waiting ...'.format(update_interval),
             log_signal=string_to_bool(os.environ.get('SHOW_WAIT_COUNTDOWN', 'true')))
