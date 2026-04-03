FROM freqtradeorg/freqtrade:stable

USER root
RUN pip install requests python-dotenv --quiet

# Copy config and strategy
COPY freqtrade-config/ /freqtrade/user_data/

# Copy execution scripts
COPY execution/ /freqtrade/execution/

# Copy startup script
COPY start.sh /freqtrade/start.sh
RUN chmod +x /freqtrade/start.sh

USER ftuser
EXPOSE 8080

CMD ["/freqtrade/start.sh"]
