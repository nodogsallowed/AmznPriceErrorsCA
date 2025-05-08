# 📦 Telegram Deal Aggregator Bot

This is a custom Telegram Bot designed to help users **search, subscribe, and receive alerts** for deals and promotions via an interactive, user-friendly interface. Built with Python and hosted on [Railway](https://railway.app), this bot leverages the Telegram Bot API to provide real-time responses and seamless interaction.

## 🌟 Features

### ✅ Core Commands

- **/start**  
  Initiates the bot with a welcome message and introduces available commands.

- **/search**  
  Allows users to manually search for deals by keyword.  
  ✅ _Interactive response coming soon._

- **/subscribe**  
  Users can subscribe to specific deal categories.  
  ✅ _Subscription preferences stored and tied to Telegram user ID._

- **/alert**  
  Triggers alerts for newly scraped or relevant deals.  
  ✅ _Automated alerts based on category subscriptions._

- **/scrape**  
  Starts a scraping process for the latest deals (admin-only).  
  ✅ _Includes real-time status feedback (e.g., “Scraping in progress…”)_

### 🔘 Inline Keyboard Support

All commands are transitioning to support **inline button-based interaction**, enabling users to interact without needing to type slash commands.

- Tapable buttons for `/search`, `/subscribe`, and `/alert`
- Inline popups and category selectors

### 🧠 Backend Logic

- Built with **Python** and **python-telegram-bot**
- Commands handled using a centralized dispatcher
- Logging enabled for all user interactions
- User preferences and subscription data stored in a persistent JSON or database (depending on deployment)

### 🛠 Deployment

- Hosted on [Railway](https://railway.app)
- Uses webhook polling to process messages in real-time
- Environment variables securely managed via Railway UI
