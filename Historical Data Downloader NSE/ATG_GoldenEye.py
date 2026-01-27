from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException
import sys
import webbrowser
import os

api_key = "jc05rr20uksos0hc"      # Your app's API key
api_secret = "8lkcag640fxypwahjzdu6csewm8n8504"    # Your app's API secret
# It's common that request tokens expire quickly. Prefer passing this at runtime
# (env var or prompt) instead of hardcoding a value that will expire.
# Prefer providing a fresh request token via environment variable so you don't
# need to edit the script each time. The script will fall back to the hardcoded
# token if the env var is not set.
request_token = os.environ.get("KITE_REQUEST_TOKEN", "GwOP44Dk7A9SXGsEVRjetRxpxn4CRNnJ")

kite = KiteConnect(api_key=api_key)

def generate_and_print_session(rt):
	try:
		data = kite.generate_session(rt, api_secret=api_secret)
	except TokenException as e:
		# Provide actionable guidance for the common cases
		print("TokenException: Token is invalid or has expired.")
		print("Common causes:")
		print(" - The request_token has expired (they are single-use and short-lived).")
		print(" - The request_token was already used to create a session.")
		print(" - The api_secret provided is incorrect for the api_key.")
		print()
		print("Detailed error from kiteconnect:\n", e)
		return None

	access_token = data.get("access_token")
	if access_token:
		print("Access Token:", access_token)
		# Optionally set it on the kite instance for further calls in this run
		kite.set_access_token(access_token)
		return access_token
	else:
		print("Session created but no access_token returned:", data)
		return None


def prompt_for_request_token():
	# Show the login URL and open it in the default browser so the user can authenticate
	try:
		login_url = kite.login_url()
	except Exception as e:
		print("Unable to generate login URL. Check your api_key. Error:", e)
		return ""

	print("Open this URL in your browser to login and obtain a request_token:")
	print(login_url)
	try:
		webbrowser.open(login_url)
	except Exception:
		# Not fatal — user can copy/paste the URL
		pass

	try:
		rt = input("After login, paste the 'request_token' from the redirect URL (or press Enter to cancel): ").strip()
	except Exception:
		rt = ""
	return rt


# Try with the current request_token; if it fails, prompt the user to open the login URL and paste a new one.
token = None
if request_token:
	token = generate_and_print_session(request_token)

if not token:
	new_rt = prompt_for_request_token()
	if not new_rt:
		print("No request_token provided. Exiting.")
		sys.exit(1)

	token = generate_and_print_session(new_rt)
	if not token:
		print("Failed to generate session with the provided request_token. Exiting.")
		sys.exit(1)
