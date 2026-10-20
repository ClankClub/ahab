# Privacy

Ahab is a personal assistant that one person runs on their own computer for themselves. This covers what happens to data when an instance of Ahab is connected to a Google account (or any other service).

- **Who uses it.** Each instance has one owner. There are no other users, no sign-ups and no hosted service.
- **What it reads.** Only what the owner connects it to, such as a calendar, mailbox or files they share with it, and only to do what the owner asks.
- **Where data goes.** It stays on the owner's computer: Ahab's memory files and the owner's own n8n instance, which holds the Google sign-in. Text Ahab works with is sent to the language model the owner has configured (by default the DeepSeek API) so the model can read it. Nothing is sold, used for advertising, or shared with anyone else.
- **Google user data.** Data obtained through Google APIs is used only to provide the assistant's features to its owner, and its use follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.
- **Removing access.** The owner can revoke access at any time at https://myaccount.google.com/permissions, and delete Ahab's local data by deleting its `home/` folder.

Questions: open an issue on this repository.
