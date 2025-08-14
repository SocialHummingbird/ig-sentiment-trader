import getpass, keyring
key = getpass.getpass("Paste your OpenAI API key: ")
keyring.set_password("openai", "api_key", key)
print("Stored in Windows Credential Manager.")
