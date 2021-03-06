import datetime
import time

# import click
import sys
import yaml

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.blocking import BlockingScheduler

from antminer_autotune.antminer import Antminer
from antminer_autotune.util import merge_dicts

DEFAULT_CONFIG = {
    'min_temp': 72,
    'max_temp': 76,
    'dec_time': 30,
    'inc_time': 900,
    'refresh_time': 5
}

DEFAULT_CONFIG_FILENAME = 'config.yml'


def throttle(device, jobs, **kwargs):
    """

    :type device: Antminer
    :type job: apscheduler.Job
    """
    try:
        temperature = device.temperature
        elapsed = device.elapsed
        api_frequency = device.api_frequency
        hw_err = device.hardware_error_rate
    except Exception as e:
        print('{:<16} -'.format(device.host), 'Failed to collect api data: ', e)
        return e

    print('{:<16} -'.format(device.host),
          'temp: {:>2}   '.format(temperature),
          'freq: {:>3}   '.format(api_frequency),
          'uptime: {:>6}   '.format(elapsed),
          'hr: {:>7.2f}   '.format(device.hash_rate_avg),
          'h5: {:>7.2f}   '.format(device.hash_rate_5s),
          'hw: {:>7.4}%'.format(hw_err))

    # TODO - Debounce temperature.
    new_freq = None
    if api_frequency > device.model['max_freq']:
        new_freq = device.model['max_freq']

    elif (api_frequency > device.model['min_freq'] and
                  temperature > device.model['max_temp'] and
                  elapsed > device.model['dec_time']):  # cool-down logic
        new_freq = device.prev_frequency()

    elif (api_frequency < device.model['max_freq'] and
                  temperature < device.model['min_temp'] and
                  elapsed > device.model['inc_time']):  # speed-up logic
        new_freq = device.next_frequency(int(abs(temperature - device.model['min_temp']) / 3) + 1)

    if new_freq:
        [job.pause() for job in jobs]
        print('{:<16} -'.format(device.host), 'setting frequency to:', new_freq)
        try:
            device.reset_config()
            device.frequency = new_freq
            device.push_config(True)
            time.sleep(15)
        except Exception as e:  # TODO - Investigate possible failures and retry options.
            print('{:<16} -'.format(device.host), 'failed to set frequency!', e)

        [job.resume() for job in jobs]


def do_thing(device, command, value, jobs, **kwargs):
    [job.pause() for job in jobs]
    print('{:<16} -'.format(device.host), 'setting {} to:'.format(command), value)
    try:
        device.reset_config()
        setattr(device, command, value)
        device.push_config(True)
        time.sleep(15)
    except Exception as e:
        print('{:<16} -'.format(device.host), 'failed to set {}!'.format(command), e)

    [job.resume() for job in jobs]


def listener(event):
    print(event)
    print(event.exception)


# TODO - Click doesn't work easily on Python 3. Investigate alternative cli library.
# @click.command()
# @click.option('--config', type=click.File())
def main(*args, **kwargs):
    config_filename = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG_FILENAME
    config = DEFAULT_CONFIG.copy()
    miners = []

    try:
        config_file = yaml.load(open(config_filename))
        config.update(config_file['defaults'])
        miners.extend(config_file['miners'])
    except FileNotFoundError:
        print('Config file \'{}\' was not found.'.format(config_filename))
        exit(1)
    except KeyError as e:
        print('Config did not contain section {}.'.format(e))
        exit(1)

    # print(config)
    # print(miners)

    scheduler = BlockingScheduler(job_defaults={'coalesce': True})
    scheduler.add_listener(listener, EVENT_JOB_ERROR)

    for idx, miner in enumerate(miners):
        schedules = miner.pop('schedule', [])
        device = Antminer(**miner)
        job_config = merge_dicts(config, {'jobs': [], 'idx': idx})
        job = scheduler.add_job(throttle, 'interval', args=(device,), kwargs=job_config,
                                misfire_grace_time=30, seconds=config['refresh_time'],
                                next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=idx * 0.2))
        job_config['jobs'].append(job)
        for schedule in schedules:
            print(schedule)
            trigger_args = {k: schedule.pop(k) for k in schedule.copy() if
                            k in ['year', 'month', 'day', 'week', 'day_of_week', 'hour', 'minute', 'second',
                                  'start_date', 'end_date']}
            print(trigger_args)
            job = scheduler.add_job(do_thing, 'cron', args=(device, schedule['command'], schedule['value'],),
                                    kwargs=job_config, **trigger_args)
            job_config['jobs'].append(job)


    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == '__main__':
    main()
