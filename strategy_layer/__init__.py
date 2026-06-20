"""Trading Strategy Layer.

Orchestrates strategy lifecycle:
  - Fetch market data from futures_demo
  - Calculate indicators and generate signals
  - Apply risk checks
  - Translate signals to orders
  - Place orders via trading_execution_layer
"""
