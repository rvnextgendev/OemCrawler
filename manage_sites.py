"""Register a reviewed public/form-login HTTPS site without editing Python code."""
import argparse
import json
from pathlib import Path
from app.site_profiles import profiles, normalize, same_origin
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--id', required=True)
parser.add_argument('--url', required=True, help='Starting HTTPS URL')
parser.add_argument('--name', required=True)
parser.add_argument('--prefix',default='/')
parser.add_argument('--login-url')
parser.add_argument('--username-env',default='SITE_USERNAME')
parser.add_argument('--password-env',default='SITE_PASSWORD')
parser.add_argument('--username-field',default='username')
parser.add_argument('--password-field',default='password')
parser.add_argument('--form-selector',default='form')
parser.add_argument('--success-selector',help='Required for login: element visible only after successful authentication')
args=parser.parse_args()
from urllib.parse import urlsplit
u=urlsplit(args.url)
if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443):
    parser.error('Use an HTTPS URL without credentials and the default port')
profile={'name':args.name,'origin':'https://' + u.hostname,'path_prefix':args.prefix,
         'mode':'form_login' if args.login_url else 'public','start_url':args.url,
         'pace_seconds':10,'robots_required':True}
if not normalize(args.url,profile):
    parser.error('Start URL must be an HTML page inside the selected path prefix')
if args.login_url:
    if not args.success_selector or not same_origin(args.login_url,profile):
        parser.error('Login requires an exact same-origin URL and a success selector')
    profile['login']={'url':args.login_url,'form_selector':args.form_selector,
        'username_field':args.username_field,'password_field':args.password_field,
        'username_env':args.username_env,'password_env':args.password_env,
        'success_selector':args.success_selector}
p=Path(__file__).with_name('sites.json')
items=json.loads(p.read_text())
if args.id in items:
    parser.error('Profile id exists; edit sites.json to update it')
items[args.id]=profile
# Validate using a temporary file; never replace working configuration on failure.
import os
import tempfile
with tempfile.TemporaryDirectory() as temp:
    candidate=Path(temp)/'sites.json'
    candidate.write_text(json.dumps(items))
    import app.site_profiles as rules
    original=rules.PROFILES_FILE
    rules.PROFILES_FILE=candidate
    try:
        rules.profiles()
    finally:
        rules.PROFILES_FILE=original
p.write_text(json.dumps(items,indent=2)+'\n')
print('Added profile. Rebuild Docker services to apply. Configure named credential env variables separately for login profiles.')
