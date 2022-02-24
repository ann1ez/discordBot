# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
import random
from report import Report

# Set up logging to the console
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# There should be a file called 'token.json' inside the same folder as this file
token_path = 'tokens.json'
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    # If you get an error here, it means your token is formatted incorrectly. Did you put it in quotes?
    tokens = json.load(f)
    discord_token = tokens['discord']
    perspective_key = tokens['perspective']


class ModBot(discord.Client):
    def __init__(self, key):
        intents = discord.Intents.default()
        super().__init__(command_prefix='.', intents=intents)
        self.group_num = None
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.reports = {} # Map from user IDs to the state of their report
        self.perspective_key = key
        self.currReport = None

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search('[gG]roup (\d+) [bB]ot', self.user.name)
        if match:
            self.group_num = match.group(1)
        else:
            raise Exception("Group number not found in bot's name. Name format should be \"Group # Bot\".")

        # Find the mod channel in each guild that this bot should report to
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f'group-{self.group_num}-mod':
                    self.mod_channels[guild.id] = channel

    async def on_message(self, message):
        '''
        This function is called whenever a message is sent in a channel that the bot can see (including DMs). 
        Currently the bot is configured to only handle messages that are sent over DMs or in your group's "group-#" channel. 
        '''
        # Ignore messages from the bot 
        if message.author.id == self.user.id:
            return

        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        # Handle a help message
        if message.content == Report.HELP_KEYWORD:
            reply =  "Use the `report` command to begin the reporting process.\n"
            reply += "Use the `cancel` command to cancel the report process.\n"
            await message.channel.send(reply)
            return

        author_id = message.author.id
        responses = []

        # Only respond to messages if they're part of a reporting flow
        if author_id not in self.reports and not message.content.startswith(Report.START_KEYWORD):
            return

        # If we don't currently have an active report for this user, add one
        if author_id not in self.reports:
            self.reports[author_id] = Report(self)

        # Let the report class handle this message; forward all the messages it returns to uss
        responses = await self.reports[author_id].handle_message(message)
        for r in responses:
            await message.channel.send(r)

        # If the report is complete or cancelled, remove it from our map
        if self.reports[author_id].report_complete():
            # If the report was properly completed, finish flow on moderator's side
            if not message.content == 'cancel':
                reported_m = self.reports[author_id].reportedMessage
                mod_channel = self.mod_channels[reported_m.guild.id]
                await mod_channel.send(self.report_mod_message(message))
            self.reports.pop(author_id)

    def report_mod_message(self, message):       
        author_id = message.author.id
        self.currReport = self.reports[author_id]
        reported_m = self.reports[author_id].reportedMessage
        
        # Foward the complete report to the mod channel
        reply = "NEW REPORT \nmade by `" + message.author.name + "` regarding a post by `" + reported_m.author.name + "`"
        reply += "\n• The message reported falls under **" + self.reports[author_id].broadCategory + "**"
        reply += "\n• And is more specifically related to **" + self.reports[author_id].specificCategory + "**"
        reply += "\n• Here is an optional message from the reporter: **" + self.reports[author_id].optionalMessage + "**"
        reply += "\n• Would the reporter like to no longer see posts from the same user? **" + self.reports[author_id].postVisibility + "**"       
        if self.reports[author_id].postVisibility == 'yes':
            reply += "\nHow would the reporter like to change the status of the offending user's relationship with them? **" + self.reports[author_id].userVisibility + "**"
        reply += "\n\n And here is the message content: ```" + reported_m.content + "```"
        if self.currReport.broadCategory == 'Misinformation':
            reply += "\nIs a response necessary? Please enter `yes`, `no`, or `unclear`."
        else:
            reply += "\nIs a response necessary? Please enter `yes` or `no`."
        return reply

    async def handle_mod_message(self, message): 
        mod_channel = self.mod_channels[message.guild.id]
        if message.content == 'yes':
            # Post needs to be removed
            await self.currReport.reportedMessage.add_reaction('❌') 
            await mod_channel.send("This post has been deleted. This post removal is symbolized by the ❌ reaction on it.")
        elif self.currReport.broadCategory == 'Misinformation':
            # If not misinfo but high risk, add warning (DIFF from flow)
            if message.content == 'no' and self.currReport.specificCategory in {'Elections', 'Covid-19', 'Other Health or Medical'}:
                await self.currReport.reportedMessage.add_reaction('⭕') 
                await mod_channel.send("This post has a warning label now. This warning is symbolized by the ⭕ reaction on it.")
            if message.content == 'unclear':
                # send to fact checker function
                if random.randrange(100) < 50:
                    await self.currReport.reportedMessage.add_reaction('❌') 
                    await mod_channel.send("This post has been classified as false by the fact checker so it had been deleted. This post removal is symbolized by the ❌ reaction on it.")
                else:
                    await self.handle_special_cases() 
                    await mod_channel.send("This post has been classified as true by the fact checker so it has only been de-prioritized and given a warning label. These actions are symbolized by the 🔻 and ⭕ reactions respectively.")
        return 
    
    async def handle_special_cases(self):
        # Check if it is high risk
        if self.currReport.specificCategory in {'Elections', 'Covid-19', 'Other Health or Medical'}:
            await self.currReport.reportedMessage.add_reaction('🔻') # this emoji represents de-prioritization (ie shown to less people) 
            await self.currReport.reportedMessage.add_reaction('⭕') # this emoji represents a warning label
        return

    async def handle_channel_message(self, message):
        # Send the info to mod funtion if necessary
        if message.channel.name == f'group-{self.group_num}-mod':
            await self.handle_mod_message(message)

        # Only handle messages sent in the "group-#" channel
        if not message.channel.name == f'group-{self.group_num}':
            return

        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')

        scores = self.eval_text(message)
        await mod_channel.send(self.code_format(json.dumps(scores, indent=2)))

    def eval_text(self, message):
        '''
        Given a message, forwards the message to Perspective and returns a dictionary of scores.
        '''
        PERSPECTIVE_URL = 'https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze'

        url = PERSPECTIVE_URL + '?key=' + self.perspective_key
        data_dict = {
            'comment': {'text': message.content},
            'languages': ['en'],
            'requestedAttributes': {
                                    'SEVERE_TOXICITY': {}, 'PROFANITY': {},
                                    'IDENTITY_ATTACK': {}, 'THREAT': {},
                                    'TOXICITY': {}, 'FLIRTATION': {}
                                },
            'doNotStore': True
        }
        response = requests.post(url, data=json.dumps(data_dict))
        response_dict = response.json()

        scores = {}
        for attr in response_dict["attributeScores"]:
            scores[attr] = response_dict["attributeScores"][attr]["summaryScore"]["value"]

        return scores

    def code_format(self, text):
        return "```" + text + "```"


client = ModBot(perspective_key)
client.run(discord_token)
