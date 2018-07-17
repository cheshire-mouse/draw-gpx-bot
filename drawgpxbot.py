#!/usr/bin/env python
# -*- coding: utf-8 -*-


"""Draw GPX Telegram bot

Draw GPX Bot, you send him your gps track, and get back a nice picture with a map background

Usage:
Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""

__author__ = "https://github.com/cheshire-mouse"
__license__ = "WTFPL v. 2"
__version__ = "0.1"

from telegram import TelegramError
from telegram.ext import Updater, CommandHandler, MessageHandler
from telegram.ext.filters import Filters
import logging
import re
import os.path
from lxml import etree
import json
import subprocess
import ConfigParser
from datetime import datetime
import dateutil.parser
from dateutil import tz
from geographiclib.geodesic import Geodesic
import math
import argparse

# Read config
config = ConfigParser.RawConfigParser()
config.read('drawgpxbot.cfg')
options = dict(config.items('general'))
 
# Enable logging
logging.basicConfig(filename=options['file_log'],
                    format=u'%(asctime)s %(name)s %(levelname)s %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)


class GPXNik4FailureException(Exception):
    """ Nik4 script returned some error """
    def __init__(self, message):
        self.message = message

class GPXParseException(Exception):
    """ Something wrong with XML format """
    def __init__(self, message):
        self.message = message

class ArgumentParseError(Exception):
    """ Can't parse command arguments """
    def __init__(self, message):
        self.message = message

class SilentArgumentParser(argparse.ArgumentParser):
    """ Argument Parser, no message printing, only exceptions """
    def error(self, message):
        raise ArgumentParseError(('Argument parsing error: %s\n') % (message))

class Gpx2JSONTarget:
    """ XML handler """
    __xmlns = "{http://www.topografix.com/GPX/1/1}"
    __dt_zero = datetime(1970,1,1,tzinfo=tz.gettz('UTC'))
    __min_movespeed = 0.3 # ~1km/h
    def __init__(self):
        logger.info('read gpx XML')
        self.__bbox = {'xmin':1000, 'ymin':1000, 'xmax':-1000, 'ymax':-1000 }
        self.__num_points = 0
        self.__track = list()
        self.__has_timestamps = True
        self.__last_tag = None
        self.__reading_segment = False
        return
    def start(self, tag, attrib):
        if (tag == self.__xmlns + "trk"): 
            logger.debug('track start')
        elif (tag == self.__xmlns + "trkseg"): 
            logger.debug('track segment start')
            self.__track.append(list())
            self.__reading_segment = True
        elif (tag == self.__xmlns + "trkpt"):
            if ( self.__has_timestamps 
                    and len(self.__track[-1]) > 0 
                    and len(self.__track[-1][-1]) < 3 ):
                self.__has_timestamps = False
            lon = float(attrib["lon"])
            lat = float(attrib["lat"])
            self.__bbox['xmin'] = min(self.__bbox['xmin'],lon)
            self.__bbox['xmax'] = max(self.__bbox['xmax'],lon)
            self.__bbox['ymin'] = min(self.__bbox['ymin'],lat)
            self.__bbox['ymax'] = max(self.__bbox['ymax'],lat)
            self.__track[-1].append({'lon':lon,'lat':lat})
            self.__num_points += 1
        self.__last_tag = tag
    def end(self, tag):
        if (tag == self.__xmlns + "trk"): 
            logger.debug('track end')
        elif (tag == self.__xmlns + "trkseg"): 
            logger.debug('track segment end')
            self.__reading_segment = False
        self.__last_tag = None
        return
    def data(self, data):
        if (self.__last_tag == self.__xmlns + "time"
                and self.__reading_segment):
            #dt.utcfromtimestamp(int((dateutil.parser.parse('2018-07-07T11:47:47Z').astimezone(tz.gettz('UTC')) - dt(1970,1,1,tzinfo=tz.gettz('UTC')) ).total_seconds()))
            dt = dateutil.parser.parse(data).astimezone(tz.gettz('UTC'))
            timestamp = int((dt - self.__dt_zero).total_seconds())
            self.__track[-1][-1]['timestamp'] = timestamp
        return
#    def comment(self, text):
#        return
    def close(self):
        logger.debug('gpx has timestamps: {}'.format(self.__has_timestamps))
        logger.info("end of the gpx XML,"+
                " {0} points found".format(self.__num_points))
        return 
    def calc_statistics(self):
        statistics = dict()
        lines = list()
        n = 0
        for track_segm in self.__track:
            for i in range(1,len(track_segm)):
                n += 1
                p1 = track_segm[i-1]
                p2 = track_segm[i]
                dist = Geodesic.WGS84.Inverse(p1['lat'],p1['lon'],p2['lat'],p2['lon'])['s12']
                ln = [dist]
                if self.__has_timestamps:
                    if p2['timestamp'] == p1['timestamp']:
                        continue
                    else:
                        ln.append(p2['timestamp'] - p1['timestamp'])
                        #logger.debug('{} lat1 {} lon1 {} lat2 {} lon2 {} dist {} tm {} spd {}'.format(
                        #    n,
                        #    p1['lat'],
                        #    p1['lon'],
                        #    p2['lat'],
                        #    p2['lon'],
                        #    dist,
                        #    p2['timestamp'] - p1['timestamp'],
                        #    dist/(p2['timestamp'] - p1['timestamp'])
                        #    ));
                lines.append(ln)
        trk_length = sum([l[0] for l in lines])
        statistics['length'] = trk_length
        if self.__has_timestamps:
            trk_time = sum([l[1] for l in lines])
            trk_movelength = sum([l[0] for l in lines if l[0]/l[1]>self.__min_movespeed])
            trk_movetime = sum([l[1] for l in lines if l[0]/l[1]>self.__min_movespeed])
            #to estimate maxspeed we will take at least <sec> seconds interval
            sec = 5.0
            npnt = int(math.ceil( float( sec / lines[0][1]) ))
            npnt = min(npnt,len(lines))
            logger.debug('npnt: {}'.format(npnt));
            #trk_maxspeed = max([l[0]/l[1] for l in lines])
            trk_maxspeed = 0.0
            for i in range(npnt-1,len(lines)):
                speed = ( sum([lines[j][0] for j in range(i-npnt+1,i+1)]) /
                        sum([lines[j][1] for j in range(i-npnt+1,i+1)]) )
                trk_maxspeed = max([trk_maxspeed, speed])
            trk_starttime = self.__track[0][0]['timestamp']
            trk_endtime = self.__track[-1][-1]['timestamp']
            statistics['time'] = trk_time
            statistics['speed'] = trk_length/trk_time
            statistics['movetime'] = trk_movetime
            statistics['movespeed'] = trk_movelength/trk_movetime
            statistics['maxspeed'] = trk_maxspeed
            statistics['starttime'] = trk_starttime
            statistics['endtime'] = trk_endtime
        self.__statistics = statistics
        logger.debug('gpx statistics: {}'.format(statistics)) 
        return statistics
    def get_num_points(self):
        return self.__num_points
    def get_json(self):
        if self.__num_points == 0:
            raise GPXParseException("GPX file is empty, cannot create JSON")
        track_json = {
            "type" : "FeatureCollection",
            "features" : list()
        }
        for track_segm in self.__track:
            coordinates = [ [ p['lon'], p['lat'] ] for p in track_segm ]
        track_json['features'].append(
            {
                "type" : "Feature",
                "properties" : { "stroke": "#ff2b00", "stroke-width": 2, "stroke-opacity": 1 },
                "geometry" : { "type" : "LineString", "coordinates" : coordinates }
            })
        return json.dumps(track_json,indent=2)
    def get_bbox(self):
        if self.__num_points == 0:
            raise GPXParseException("GPX file is empty, cannot create bbox")
        return self.__bbox
    def get_multiline(self):
        if self.__num_points == 0:
            raise GPXParseException("GPX file is empty, cannot create bbox")
        return self.__track

def timestamp2hhmmss(ts):
    hh = ts/3600
    mm = ts/60 - hh*60
    ss = ts - mm*60 - hh*3600
    return "{:02d}:{:02d}:{:02d}".format(hh,mm,ss)


def gpx_draw(gpx_path,fmt,zoom,color,width):
    image_path = ''.join([options['folder_images'],'/',
            os.path.splitext(os.path.basename(gpx_path))[0],
            '.',fmt])
    json_path = ''.join([
            os.path.splitext(gpx_path)[0],
            '.geojson'])
    gpx = Gpx2JSONTarget();
    parser = etree.XMLParser(target=gpx);
    f=open(gpx_path,"r");
    etree.parse(f,parser);
    f.close();
    f=open(json_path,"w");
    f.write(gpx.get_json())
    f.close();
    logger.debug('created json {0}'.format(json_path))
    bbox = gpx.get_bbox()
    logger.debug('json bbox {0}'.format(str(bbox)))
    # add margins
    xmin = bbox['xmin'] - (bbox['xmax'] - bbox['xmin']) * 0.05 
    ymin = bbox['ymin'] - (bbox['ymax'] - bbox['ymin']) * 0.05 
    xmax = bbox['xmax'] + (bbox['xmax'] - bbox['xmin']) * 0.05 
    ymax = bbox['ymax'] + (bbox['ymax'] - bbox['ymin']) * 0.05 
    cmd_nik4 = [options['cmd_nik4']]
    if 'folder_fonts' in options:
        cmd_nik4 += ['--fonts',options['folder_fonts']]
    cmd_nik4 += ['--vars',]
    cmd_nik4 += ['track_color={}'.format(color)]
    cmd_nik4 += ['track_width={}'.format(width)]
    cmd_nik4 += [
        "-b",str(xmin),str(ymin),str(xmax),str(ymax),'-z',str(zoom),
        '-f',fmt,options['mapnik_style_xml'],image_path
        ]
    logger.debug(' '.join(cmd_nik4))
    retcode = subprocess.call(cmd_nik4)
    if retcode != 0:
        raise GPXNik4FailureException('Nik4 returned nonzero code '.format(retcode))
    return image_path

# Jobs

def job_gpx_draw(bot, job):
    """There will be some mapnik magic here"""
    file_name = u""
    try:
        chat_id = job.context['chat_id']
        zoom = job.context['zoom']
        fmt = job.context['format']
        color = job.context['color']
        width = job.context['width']
        file_name = job.context['document'].file_name
        logger.info(u'start job to draw gpx {0} (fmt={1}, zoom={2})'.format(file_name,fmt,zoom))
        fl = job.context['document'].get_file()
        fl_path = ''.join([options['folder_gpx'], '/track.gpx'])
        fl.download(custom_path=fl_path)
        logger.debug(u'downloaded gpx {0} to {1}'.format(file_name,fl_path))
        image_path = gpx_draw(fl_path,fmt,zoom,color,width)
        logger.debug(u'nik4 finished with {0}'.format(file_name))
        f=open(image_path,"rb");
        if fmt=='png':
            #bot.send_photo(chat_id,photo=f,caption=file_name)
            bot.send_document(chat_id,document=f,caption=file_name,
                    timeout=300)
        else:
            bot.send_document(chat_id,document=f,caption=file_name,
                    timeout=300)
        f.close();
        logger.info(u'image successfuly sent for {0}'.format(file_name))
    except TelegramError as e:
        logger.error('Cant upload image file: {}'.format(e))
        bot.send_message(job.context['chat_id'],text=u'Ничего не вышло. Не могу загрузить' +
            u' трек {0}'.format(file_name))
    except GPXParseException as e:
        logger.error('Cant parse gpx file: {}'.format(e.message))
        bot.send_message(job.context['chat_id'],text=u'Ничего не вышло. Чего-то не то с GPX,' +
            u' трек {0}'.format(file_name))
    except Exception as e:
        if hasattr(e, 'message'):
            logger.error('Cant create image file: {}'.format(e.message))
        else:
            logger.error('Cant create image file: {}'.format(e))
        bot.send_message(job.context['chat_id'],text=u'Ничего не вышло. Все сломалось,' +
            u' трек {0}'.format(file_name))

def job_gpx_stat(bot, job):
    """download track and calc statistics like length, speed, ..."""
    file_name = u""
    try:
        chat_id = job.context['chat_id']
        file_name = job.context['document'].file_name
        logger.info(u'start job to collect stat on {0}'.format(file_name))
        fl = job.context['document'].get_file()
        fl_path = ''.join([options['folder_gpx'], '/track.gpx'])
        fl.download(custom_path=fl_path)
        logger.debug(u'downloaded gpx {0} to {1}'.format(file_name,fl_path))
        gpx = Gpx2JSONTarget();
        parser = etree.XMLParser(target=gpx);
        f=open(fl_path,"r");
        etree.parse(f,parser);
        f.close();
        statistics = gpx.calc_statistics()
        logger.debug(u'stats collected ({0})'.format(file_name))
        msg  = u"Статистика по {0}\n".format(file_name)
        if 'length' in statistics:
            msg += u"\nдлина: {:.1f} км".format(statistics['length']/1000.0)
        if 'movespeed' in statistics:
            msg += u"\nскорость: {:.1f} км/ч".format(statistics['movespeed']*3.6)
        if 'maxspeed' in statistics:
            msg += u"\nскорость (макс): {:.1f} км/ч".format(statistics['maxspeed']*3.6)
        if 'time' in statistics:
            msg += u"\nвремя: {}".format(timestamp2hhmmss(statistics['time']))
        if 'movetime' in statistics:
            msg += u"\nвремя в движении: {}".format(timestamp2hhmmss(statistics['movetime']))
        if 'starttime' in statistics:
            msg += u"\nначало: {}".format(datetime.fromtimestamp(statistics['starttime'],tz.gettz()).strftime('%c %Z'))
        if 'endtime' in statistics:
            msg += u"\nконец: {}".format(datetime.fromtimestamp(statistics['endtime'],tz.gettz()).strftime('%c %Z'))
        bot.send_message(chat_id,text=msg)
        bot.send_location(chat_id, disable_notification = True,
            latitude=gpx.get_multiline()[0][0]['lat'],
            longitude=gpx.get_multiline()[0][0]['lon'])
        logger.info(u'stats successfuly sent for {0}'.format(file_name))
    except GPXParseException as e:
        logger.error('Cant parse gpx file: {}'.format(e.message))
        bot.send_message(job.context['chat_id'],text=u'Ничего не вышло. Чего-то не то с GPX,' +
            u' трек {0}'.format(file_name))
    except Exception as e:
        if hasattr(e, 'message'):
            logger.error('Cant collect stats: {}'.format(e.message))
        else:
            logger.error('Cant collect stats: {}'.format(e))
        bot.send_message(job.context['chat_id'],text=u'Ничего не вышло. Все сломалось,' +
            u' трек {0}'.format(file_name))


# Command handlers

def on_cmd_help(bot, update):
    help_message  = 'Рисую GPS треки. Безвозмездно (т.е. даром)\n'
    help_message += 'Файлы шли в GPX формате\n\n'
    help_message += '/help    - справка\n'
    help_message += '/license - лицензия/копирайты\n'
    help_message += '/gpxname - имя последнего трека\n'
    help_message += '/gpxstat - статистика по последнему треку\n'
    help_message += '/gpxdraw [<опции>] - нарисовать\n'
    help_message += '                     последний трек\n'
    help_message += '         опции:\n'
    help_message += '           -format - png|svg\n'
    help_message += '           -zoom   - зум 1-15\n'
    help_message += '           -color  - цвет\n'
    help_message += '              red|orange|yellow|green\n'
    help_message += '              blue|indigo|violet\n'
    help_message += '           -width  - ширина 1-50'
    update.message.reply_text(help_message)

def on_cmd_license(bot, update):
    lic_message  = 'Лицензия на карту\n'
    lic_message += 'принадлежит ребятам из OpenStreetMap\n'
    lic_message += 'https://www.openstreetmap.org/copyright'
    update.message.reply_text(lic_message)


def on_cmd_gpxdraw(bot, update, args, job_queue, chat_data):
    """Add job to draw last GPX track"""
    logger.debug(u'cmd gpxdraw, args {0}'.format(str(args)))
    chat_id = update.message.chat_id
    try:
        parser = SilentArgumentParser(add_help=False)
        parser.add_argument("-format",required=False, 
            choices=['png','svg'], default='png')
        parser.add_argument("-zoom",required=False, 
            type = int, choices = range(1,16), default=12)
        parser.add_argument("-color",required=False, 
            choices=['red','orange','yellow','green','blue','indigo','violet'], 
            default=options['track_color'])
        parser.add_argument("-width",required=False, 
            type = int, choices = range(1,51), default=options['track_width'])

        cmd_options = parser.parse_args(args)

        if 'last gpx' not in chat_data:
            update.message.reply_text('Не видел никаких треков')
            return
            return
        else:
            logger.info(u'add job to draw {0}'.format(chat_data['last gpx'].file_name))
            update.message.reply_text(u'Добавил в список дел:'+
                u' нарисовать {0}'.format(chat_data['last gpx'].file_name))
            job_queue.run_once(job_gpx_draw,1,
                context={
                    'chat_id':chat_id,
                    'format':cmd_options.format,
                    'zoom':cmd_options.zoom,
                    'color':cmd_options.color,
                    'width':cmd_options.width,
                    'document':chat_data['last gpx']
                }
            )

    except ArgumentParseError as e:
        logger.error('cmd args parse error: {}'.format(e.message))
        update.message.reply_text('Ерунда какая-то. Посмотри /help')
    except (KeyError, IndexError, ValueError) as e:
        logger.error('cant add drawing job: {}'.format(e))
        update.message.reply_text('Ничего не вышло. Мои глубочайшие извинения.')
        

def on_cmd_gpxname(bot, update, chat_data):
    try:
        if 'last gpx' not in chat_data:
            update.message.reply_text(u'Треков пока не получал')
        else:
            update.message.reply_text(
                 u'Последний трек: {0}'.format(chat_data['last gpx'].file_name))
    except Exception as e:
        if hasattr(e, 'message'):
            logger.error('Cant process gpxname cmd: {}'.format(e.message))
        else:
            logger.error('Cant process gpxname cmd: {}'.format(e))

def on_cmd_gpxstat(bot, update, job_queue, chat_data):
    """Add job to collect last GPX track statistics"""
    logger.debug(u'cmd gpxstat')
    chat_id = update.message.chat_id
    try:
        if 'last gpx' not in chat_data:
            update.message.reply_text('Не видел никаких треков')
            return
        else:
            logger.info(u'add job to collect stats on {0}'.format(chat_data['last gpx'].file_name))
            update.message.reply_text(u'Добавил в список дел:'+
                u' статистика по  {0}'.format(chat_data['last gpx'].file_name))
            job_queue.run_once(job_gpx_stat,1,
                context={
                    'chat_id':chat_id,
                    'document':chat_data['last gpx']
                }
            )

    except (KeyError, IndexError, ValueError) as e:
        logger.error('cant add drawing job: {}'.format(e))
        update.message.reply_text('Ничего не вышло. Мои глубочайшие извинения.')


# Message handlers

def on_document(bot, update, chat_data):
    logging.debug(u'document {0}'.format(update.message.document.file_name))
    if re.match('.*\.gpx$',update.message.document.file_name,re.I) != None:
        update.message.reply_text(u'Нашел трек: {0}'.format(update.message.document.file_name))
        chat_data['last gpx'] = update.message.document
        logger.info(u'document {0} from {1}'.format(
            update.message.document.file_name,
            update.message.from_user.name))

# Error handler

def error(bot, update, error):
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, error)

def main():
    """Run bot. RUUUUN!!!!"""
    logger.info("Release the bot!")
    logger.debug('options: {}'.format(options))

    updater = Updater(token=options['token'])
           

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("help", on_cmd_help))
    dp.add_handler(CommandHandler("license", on_cmd_license))
    dp.add_handler(CommandHandler("start", on_cmd_help))
    dp.add_handler(CommandHandler("gpxdraw", on_cmd_gpxdraw,
                                  pass_args=True,
                                  pass_job_queue=True,
                                  pass_chat_data=True))
    dp.add_handler(CommandHandler("gpxstat", on_cmd_gpxstat,
                                  pass_job_queue=True,
                                  pass_chat_data=True))
    dp.add_handler(CommandHandler("gpxname", on_cmd_gpxname,
                                  pass_chat_data=True))

     # log all errors
    dp.add_error_handler(error)

    # on messages with documents
    dp.add_handler(MessageHandler(Filters.document,on_document,
                                  pass_chat_data=True))

    # Start the Bot
    updater.start_polling(clean=True)

    # Block until you press Ctrl-C or the process receives SIGINT, SIGTERM or
    # SIGABRT. This should be used most of the time, since start_polling() is
    # non-blocking and will stop the bot gracefully.
    updater.idle()

    logger.info("I am out")

if __name__ == '__main__':
    main()
