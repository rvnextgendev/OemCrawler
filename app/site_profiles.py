"""Reviewed, server-side site profiles. No arbitrary open-proxy destinations."""
import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PROFILES_FILE = Path(os.getenv('SITE_PROFILES_FILE', Path(__file__).parents[1] / 'sites.json'))

def profiles():
    items = json.loads(PROFILES_FILE.read_text())
    for name, profile in items.items():
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
            raise ValueError('Invalid profile id')
        origin = urlsplit(profile['origin'])
        mock = profile['mode'] == 'mock' and origin.hostname == 'mock_oem' and origin.port == 8003
        if not mock:
            if (origin.scheme != 'https' or not origin.hostname or origin.port not in (None,443)
                    or origin.username or origin.password or origin.path not in ('','/')
                    or origin.query or origin.fragment):
                raise ValueError('Site origins must be HTTPS origins without credentials')
            host = origin.hostname
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError('IP literal profiles are not permitted')
            if host == 'localhost' or '.' not in host or host.endswith(('.local','.internal','.localhost')):
                raise ValueError('Private hostnames are not permitted')
        if profile['mode'] not in ('mock','public','form_login'):
            raise ValueError('Unsupported acquisition mode')
        prefix = profile.get('path_prefix','/')
        if not prefix.startswith('/') or any(s in ('.','..') for s in prefix.split('/')):
            raise ValueError('Invalid path prefix')
    return items

def same_origin(url, profile):
    try:
        u, origin = urlsplit(url), urlsplit(profile['origin'])
        return (u.scheme == origin.scheme and u.hostname == origin.hostname
                and (u.port or (443 if u.scheme=='https' else 80)) == (origin.port or (443 if origin.scheme=='https' else 80))
                and not u.username and not u.password and len(url) <= 2048)
    except ValueError:
        return False

def normalize(url, profile):
    if not same_origin(url, profile):
        return None
    u = urlsplit(url)
    path = u.path or '/'
    if ('%' in path or '\\' in path or any(s in ('.','..') for s in path.split('/'))
            or not path.startswith(profile.get('path_prefix','/'))
            or re.search(r'\.(pdf|zip|png|jpg|jpeg|gif|svg|mp4|css|js)$',path,re.I)
            or any(x in path.lower() for x in ('/login','/logout','/signin','/account','/vin/'))):
        return None
    return urlunsplit((u.scheme,u.netloc,path,u.query if profile.get('keep_query',False) else '', ''))

def allowed_network(url):
    for profile in profiles().values():
        if normalize(url,profile):
            return True
        if not same_origin(url,profile):
            continue
        u = urlsplit(url)
        if u.path == '/robots.txt' and not u.query and not u.fragment:
            return True
        login_url = profile.get('login',{}).get('url')
        if login_url and url == login_url:
            return True
    return False

def resolve(urls, requested=None):
    choices = profiles()
    if requested:
        profile = choices.get(requested)
        if profile and all(normalize(url,profile) for url in urls):
            return requested, profile
    else:
        for name,profile in choices.items():
            # Auto uses public when public and authenticated profiles share an origin.
            if profile['mode'] != 'form_login' and all(normalize(url,profile) for url in urls):
                return name,profile
    raise ValueError('No matching site profile. Select an approved profile or add the origin/path to sites.json and rebuild Docker.')

def metadata():
    return [{'id':name,'name':p['name'],'mode':p['mode'],'start_url':p['start_url'],
             'origin':p['origin'],'path_prefix':p.get('path_prefix','/')} for name,p in profiles().items()]

def public_profile(url):
    """Build a public-only profile for an explicitly submitted HTTPS origin.

    Caller must additionally validate DNS and register its time-limited proxy grant.
    Login sites still require configured profiles; credentials are never inferred.
    """
    u=urlsplit(url)
    if u.scheme!='https' or not u.hostname or u.port not in (None,443) or u.username or u.password:
        raise ValueError('Public URLs must use HTTPS without credentials')
    host=u.hostname.encode('idna').decode('ascii')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP literal destinations are not permitted')
    if '.' not in host or host.endswith(('.local','.internal','.localhost')):
        raise ValueError('Public hostname required')
    return {'name':'Public · '+host,'origin':'https://'+host,'path_prefix':'/',
            'mode':'public','start_url':url,'pace_seconds':10,'robots_required':True}
