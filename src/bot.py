from vk import Vk
from vk_user import Vk_user
from constants import action, message
from telegram import Updater, ParseMode, ReplyKeyboardHide, ReplyKeyboardMarkup
import logging
from client import Client
from telegram.dispatcher import run_async
from db import db
from poller import Poller

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)

logger = logging.getLogger(__name__)

class Bot:
    def __init__(self, token, vk_client_id):
        self.poller = Poller()
        self.updater = Updater(token=token)
        self.vk = Vk(vk_client_id)
        self.clients = Client.all_from_db()

        self.reg_actions()
        self.restore()

    def run(self):
        self.poller.async_run(self.on_update)
        self.updater.start_polling()
        self.updater.idle()
        self.poller.stop()
        self.persist()

    def persist(self):
        for _, client in self.clients.iteritems():
            client.persist()

    def restore(self):
        for _, client in self.clients.iteritems():
            self.add_poll_server(client)

    def reg_actions(self):
        dispatcher = self.updater.dispatcher
        dispatcher.addTelegramCommandHandler('start', self.start)
        dispatcher.addTelegramCommandHandler('whoami', self.whoami)
        dispatcher.addTelegramCommandHandler('pick', self.pick)
        dispatcher.addTelegramCommandHandler('details', self.details)
        dispatcher.addErrorHandler(self.error)
        dispatcher.addUnknownTelegramCommandHandler(self.unknown)
        dispatcher.addTelegramMessageHandler(self.on_message)

    def start(self, bot, update):
        chat_id = update.message.chat_id
        auth_url = self.vk.get_auth_url()
        # Send first info messages
        bot.sendMessage(chat_id=chat_id,
                text=message.WELCOME(auth_url),
                reply_markup=ReplyKeyboardHide())
        bot.sendMessage(chat_id=chat_id, text=message.COPY_TOKEN)
        # Create new client
        client = Client(next_action=action.ACCESS_TOKEN,
                        chat_id=chat_id)
        self.clients[chat_id] = client
        client.persist()

    def whoami(self, bot, update):
        chat_id = update.message.chat_id
        if not chat_id in self.clients:
            return

        client = self.clients[chat_id]
        bot.sendMessage(chat_id=chat_id,
            text=message.WHOAMI(client.vk_user.get_name()),
            reply_markup=Bot.keyboard(client.keyboard_markup()))

    def pick(self, bot, update):
        chat_id = update.message.chat_id
        if not chat_id in self.clients:
            self.start(bot, update)
            return

        client = self.clients[chat_id]
        client.seen_now()
        client.next_action = action.MESSAGE
        recepient = update.message.text[6:]
        client.expect_message_to(recepient)
        bot.sendMessage(chat_id=chat_id,
                text=message.TYPE_MESSAGE(recepient),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ReplyKeyboardHide())

    def details(self, bot, update):
        chat_id = update.message.chat_id
        if not chat_id in self.clients:
            self.start(bot, update)
            return

        client = self.clients[chat_id]
        client.seen_now()
        client.next_action = action.NOTHING
        user = client.next_recepient
        if user == None:
            return

        if user.photo != None:
            bot.sendPhoto(chat_id=chat_id, photo=user.photo)

        bot.sendMessage(chat_id=chat_id,
                text=message.USER_NAME(user.get_name()),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=Bot.keyboard(client.keyboard_markup()))

    def error(self, bot, update, error):
        logger.warn('Update "%s" caused error "%s"' % (update, error))

    def on_message(self, bot, update):
        chat_id = update.message.chat_id

        if not chat_id in self.clients:
            return self.start(bot, update)

        client = self.clients[chat_id]
        client.seen_now()

        if client.next_action == action.ACCESS_TOKEN:
            return self.on_token_message(bot, update, client)
        elif client.next_action == action.MESSAGE:
            return self.on_typed_message(bot, update, client)

        self.echo(update.message.chat_id)

    def on_token_message(self, bot, update, client):
        client.load_vk_user(update.message.text)
        name = client.vk_user.get_name()
        client.next_action = action.NOTHING
        self.add_poll_server(client)
        bot.sendMessage(chat_id=update.message.chat_id,
                text=message.TOKEN_SAVED(name),
                reply_markup=Bot.keyboard(client.keyboard_markup()))

    def on_typed_message(self, bot, update, client):
        client.next_action = action.NOTHING
        client.send_message(update.message.text)

    @run_async
    def add_poll_server(self, client):
        if client.last_used_server != None:
            self.poller.add(client.last_used_server)
        else:
            server = Vk.get_long_poll_server(token=client.vk_token,
                                             chat_id=client.chat_id)
            self.poller.add(server)

    def echo(self, chat_id):
        self.updater.bot.sendMessage(chat_id=chat_id, text=message.ECHO)

    def unknown(self, bot, update):
        bot.sendMessage(chat_id=update.message.chat_id,
                text=message.UNKNOWN)

    @staticmethod
    def keyboard(keyboard_markup):
        return ReplyKeyboardMarkup(
            keyboard_markup,
            selective=True,
            resize_keyboard=True)

    def on_update(self, updates, server):
        print str('Updates' + str(updates))
        for update in updates:
            self.process_update(update, server)

    def process_update(self, update, server):
        if len(update) == 0:
            return

        if update[0] == 4:
            # When new message received
            self.receive_vk_message(update, server)

    def receive_vk_message(self, update, server):
        chat_id = server.chat_id
        if not chat_id in self.clients:
            return

        client = self.clients[chat_id]
        client.last_used_server = server
        flags = update[2]
        from_id = update[3]
        text = update[6]
        if flags & 2 == 2:
            # Skip when message is outgoing
            return

        user = Vk_user.fetch_user(client.vk_token, from_id)
        client.add_interaction_with(user)
        self.updater.bot.sendMessage(chat_id=chat_id,
                text=message.NEW_MESSAGE(user.get_name(), text),
                reply_markup=Bot.keyboard(client.keyboard_markup()),
                parse_mode=ParseMode.MARKDOWN)
