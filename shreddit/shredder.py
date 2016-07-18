import os
import sys
import logging
import argparse
import json
import yaml
import praw
from re import sub
from datetime import datetime, timedelta
from praw.errors import (InvalidUser, InvalidUserPass, RateLimitExceeded, HTTPException, OAuthAppRequired)
from praw.objects import Comment, Submission
from shreddit.util import get_sentence


def shred(config, praw_ini=None):
    logging.basicConfig(stream=sys.stdout)
    log = logging.getLogger("shreddit")
    log.setLevel(level=logging.WARNING)

    if praw_ini:
        # PRAW won't panic if the file is invalid, so check first
        if not os.path.exists(praw_ini):
            print("PRAW configuration file \"{}\" not found.".format(praw_ini))
            return
        praw.settings.CONFIG.read(praw_ini)

    save_directory = config.get("save_directory", ".")

    r = praw.Reddit(user_agent="shreddit/4.2")
    if save_directory:
        r.config.store_json_result = True

    if config.get("verbose", True):
        log.setLevel(level=logging.DEBUG)

    try:
        # Try to login with OAuth2
        r.refresh_access_information()
        log.debug("Logged in with OAuth.")
    except (HTTPException, OAuthAppRequired) as e:
        log.warning("You should migrate to OAuth2 using get_secret.py before Reddit disables this login method.")
        try:
            try:
                r.login(config["username"], config["password"])
            except InvalidUserPass:
                r.login()  # Supply details on the command line
        except InvalidUser as e:
            raise InvalidUser("User does not exist.", e)
        except InvalidUserPass as e:
            raise InvalidUserPass("Specified an incorrect password.", e)
        except RateLimitExceeded as e:
            raise RateLimitExceeded("You're doing that too much.", e)

    log.info("Logged in as {user}.".format(user=r.user))
    log.debug("Deleting messages before {time}.".format(
        time=datetime.now() - timedelta(hours=config["hours"])))

    whitelist = config.get("whitelist", [])
    whitelist_ids = config.get("whitelist_ids", [])

    if config.get("whitelist"):
        log.debug("Keeping messages from subreddits {subs}".format(subs=", ".join(whitelist)))

    remove_things(r, config, log, get_things(r, config, log))


def get_things(r, config, log, after=None):
    limit = None
    item = config.get("item", "comments")
    sort = config.get("sort", "new")
    log.debug("Deleting items: {item}".format(item=item))
    if item == "comments":
        return r.user.get_comments(limit=limit, sort=sort)
    elif item == "submitted":
        return r.user.get_submitted(limit=limit, sort=sort)
    elif item == "overview":
        return r.user.get_overview(limit=limit, sort=sort)
    else:
        raise Exception("Your deletion section is wrong")


def remove_things(r, config, log, things):
    for thing in things:
        log.debug("Starting remove function on: {thing}".format(thing=thing))
        # Seems to be in users's timezone. Unclear.
        thing_time = datetime.fromtimestamp(thing.created_utc)
        # Exclude items from being deleted unless past X hours.
        after_time = datetime.now() - timedelta(hours=config.get("hours", 24))
        if thing_time > after_time:
            if thing_time + timedelta(hours=config.get("nuke_hours", 4320)) < datetime.utcnow():
                pass
            continue
        # For edit_only we're assuming that the hours aren't altered.
        # This saves time when deleting (you don't edit already edited posts).
        if config.get("edit_only"):
            end_time = after_time - timedelta(hours=config.get("hours", 24))
            if thing_time < end_time:
                    continue

        if str(thing.subreddit).lower() in config.get("whitelist", []) or thing.id in config.get("whitelist_ids", []):
            continue

        if config.get("whitelist_distinguished") and thing.distinguished:
            continue
        if config.get("whitelist_gilded") and thing.gilded:
            continue
        if "max_score" in config and thing.score > config["max_score"]:
            continue

        if config.get("save_directory"):
            save_directory = config["save_directory"]
            if not os.path.exists(save_directory):
                os.makedirs(save_directory)
            with open("%s/%s.json" % (save_directory, thing.id), "w") as fh:
                json.dump(thing.json_dict, fh)

        if config.get("trial_run"):  # Don't do anything, trial mode!
            log.debug("Would have deleted {thing}: '{content}'".format(
                thing=thing.id, content=thing))
            continue

        if config.get("clear_vote"):
            thing.clear_vote()

        if isinstance(thing, Submission):
            log.info("Deleting submission: #{id} {url}".format(id=thing.id, url=thing.url.encode("utf-8")))
        elif isinstance(thing, Comment):
            rep_format = config.get("replacement_format")
            if rep_format == "random":
                replacement_text = get_sentence()
            elif rep_format == "dot":
                replacement_text = "."
            else:
                replacement_text = rep_format

            msg = '/r/{3}/ #{0} with:\n\t"{1}" to\n\t"{2}"'.format(thing.id, sub(b"\n\r\t", " ",
                                                                                 thing.body[:78].encode("utf-8")),
                                                                   replacement_text[:78], thing.subreddit)
                
            if config.get("edit_only"):
                log.info("Editing (not removing) {msg}".format(msg=msg))
            else:
                log.info("Editing and deleting {msg}".format(msg=msg))

            thing.edit(replacement_text)
        if not config.get("edit_only"):
            thing.delete()

