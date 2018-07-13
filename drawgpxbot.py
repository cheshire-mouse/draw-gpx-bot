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


class Gpx2JSONTarget:
    """ XML handler """
    __xmlns = "{http://www.topografix.com/GPX/1/1}"
    def __init__(self):
        logger.info('read gpx XML')
        self.__bbox = {'xmin':1000, 'ymin':1000, 'xmax':-1000, 'ymax':-1000 }
        self.__coordinates = None
        self.__num_points = 0
        self.__track = {
            "type" : "FeatureCollection",
            "features" : list()
        }
        return
    def start(self, tag, attrib):
        if (tag == self.__xmlns + "trk"): 
            logger.debug('track start')
        elif (tag == self.__xmlns + "trkseg"): 
            logger.debug('track segment start')
            self.__coordinates = list()
            self.__track['features'].append(
                {
                    "type" : "Feature",
                    "properties" : { "stroke": "#ff2b00", "stroke-width": 2, "stroke-opacity": 1 },
                    "geometry" : { "type" : "LineString", "coordinates" : self.__coordinates }
                })
        elif (tag == self.__xmlns + "trkpt"):
            lon = float(attrib["lon"])
            lat = float(attrib["lat"])
            self.__bbox['xmin'] = min(self.__bbox['xmin'],lon)
            self.__bbox['xmax'] = max(self.__bbox['xmax'],lon)
            self.__bbox['ymin'] = min(self.__bbox['ymin'],lat)
            self.__bbox['ymax'] = max(self.__bbox['ymax'],lat)
            self.__coordinates.append([lon,lat])
            self.__num_points += 1
    def end(self, tag):
        if (tag == self.__xmlns + "trk"): 
            logger.debug('track end')
        elif (tag == self.__xmlns + "trkseg"): 
            logger.debug('track segment end')
        return
#    def data(self, data):
#        logger.debug('xml handler data, data {0}'.format(data))
#        return
#    def comment(self, text):
#        return
    def close(self):
        logger.info("end of the gpx XML,"+
                " {0} points found".format(self.__num_points))
        return 
    def get_num_points(self):
        return self.__num_points
    def get_json(self):
        if self.__num_points == 0:
            raise GPXParseException("GPX file is empty, cannot create JSON")
        return json.dumps(self.__track,indent=2)
    def get_bbox(self):
        if self.__num_points == 0:
            raise GPXParseException("GPX file is empty, cannot create bbox")
        return self.__bbox

def gpx_draw(gpx_path,zoom=None,fmt='png'):
    image_path = ''.join([options['folder_images'],'/',
            os.path.splitext(os.path.basename(gpx_path))[0],
            '.',fmt])
    json_path = ''.join([
            os.path.splitext(gpx_path)[0],
            '.geojson'])
    xmlTarget = Gpx2JSONTarget();
    parser = etree.XMLParser(target=xmlTarget);
    f=open(gpx_path,"r");
    etree.parse(f,parser);
    f.close();
    f=open(json_path,"w");
    f.write(xmlTarget.get_json())
    f.close();
    logger.debug('created json {0}'.format(json_path))
    bbox = xmlTarget.get_bbox()
    logger.debug('json bbox {0}'.format(str(bbox)))
    # add margins
    xmin = bbox['xmin'] - (bbox['xmax'] - bbox['xmin']) * 0.05 
    ymin = bbox['ymin'] - (bbox['ymax'] - bbox['ymin']) * 0.05 
    xmax = bbox['xmax'] + (bbox['xmax'] - bbox['xmin']) * 0.05 
    ymax = bbox['ymax'] + (bbox['ymax'] - bbox['ymin']) * 0.05 
    cmd_nik4 = [options['cmd_nik4'],
        "-b",str(xmin),str(ymin),str(xmax),str(ymax),'-z',str(zoom),
        '-f',fmt,options['mapnik_style_xml'],image_path]
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
        fmt = job.context['fmt']
        file_name = job.context['document'].file_name
        logger.info(u'start job to draw gpx {0} (fmt={1}, zoom={2})'.format(file_name,fmt,zoom))
        fl = job.context['document'].get_file()
        fl_path = ''.join([options['folder_gpx'], '/track.gpx'])
        fl.download(custom_path=fl_path)
        logger.debug(u'downloaded gpx {0} to {1}'.format(file_name,fl_path))
        image_path = gpx_draw(fl_path,zoom,fmt)
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

# Command handlers

def on_cmd_help(bot, update):
    help_message  = 'Я - Draw GPX, рисую GPS треки, безвозмездно (т.е. даром)\n'
    help_message += 'Присылай файл с треком и вводи одну из команд\n\n'
    help_message += '/help    - справка\n'
    help_message += '/gpxdraw - нарисовать последний трек\n'
    help_message += '/gpxdraw <zoom 1-14> <format png|svg>\n'
    help_message += '         - если точно знаешь, чего хочешь'
    update.message.reply_text(help_message)

def on_cmd_gpxdraw(bot, update, args, job_queue, chat_data):
    """Add job to draw last GPX track"""
    logger.debug(u'cmd gpxdraw, args {0}'.format(str(args)))
    chat_id = update.message.chat_id
    try:
        if 'last gpx' not in chat_data:
            update.message.reply_text('Не видел никаких треков')
            return
        elif ( len(args) not in [0,2] or
            ( len(args) == 2 and ( int(args[0]) not in range(1,15) or
                args[1] not in ['png','svg']  ) ) ):
            update.message.reply_text('Ерунда какая-то. Посмотри /help')
            return
        else:
            logger.info(u'add job to draw {0}'.format(chat_data['last gpx'].file_name))
            update.message.reply_text(u'Добавил в список дел:'+
                u' нарисовать {0}'.format(chat_data['last gpx'].file_name))
            if len(args) == 0: 
                job_queue.run_once(job_gpx_draw,1,
                    context={
                        'chat_id':chat_id,
                        'zoom':12,
                        'fmt':'png',
                        'document':chat_data['last gpx']
                    }
                )
            else:
                job_queue.run_once(job_gpx_draw,1,
                    context={
                        'chat_id':chat_id,
                        'zoom':int(args[0]),
                        'fmt':args[1],
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
        logger.info(u'document {0} passed by ...'.format(update.message.document.file_name))

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
    dp.add_handler(CommandHandler("start", on_cmd_help))
    dp.add_handler(CommandHandler("gpxdraw", on_cmd_gpxdraw,
                                  pass_args=True,
                                  pass_job_queue=True,
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
