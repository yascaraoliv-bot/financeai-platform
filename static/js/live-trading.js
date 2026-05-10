class LiveTradingDashboard {
    constructor() {
        this.symbol = 'BTCUSDT';
        this.timeframe = '15m';
        this.chart = null;
        this.candleSeries = null;
        this.volumeSeries = null;
        this.priceLines = [];
        this.socket = null;
        this.statusController = null;
        this.statusTimer = null;
        this.countdownTimer = null;
        this.lastCandleTime = 0;
        this.lastAnalysisPrice = 0;
        this.lastAnalysisVolume = 0;
        this.lastAlertSignature = '';
        this.soundEnabled = false;
        this.supportResistance = {};
        this.timeframeSeconds = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 };
        this.init();
    }

    async init() {
        this.setupChart();
        this.bindEvents();
        await this.loadAssets();
        await this.loadInitialCandles(true);
        this.fetchLiveStatus('initial');
        this.connectStreams();
        this.startCountdown();
    }

    bindEvents() {
        document.getElementById('liveAssetSelect')?.addEventListener('change', (event) => {
            this.symbol = event.target.value;
            this.resetLive(true);
        });
        document.querySelectorAll('[data-live-tf]').forEach((button) => {
            button.addEventListener('click', (event) => {
                document.querySelectorAll('[data-live-tf]').forEach((item) => item.classList.remove('active'));
                event.currentTarget.classList.add('active');
                this.timeframe = event.currentTarget.dataset.liveTf;
                this.resetLive(true);
            });
        });
        document.getElementById('liveFitChart')?.addEventListener('click', () => this.chart?.timeScale().fitContent());
        document.getElementById('liveSoundToggle')?.addEventListener('click', () => this.toggleSound());
        window.addEventListener('resize', () => this.resizeChart());
    }

    async loadAssets() {
        try {
            const response = await fetch('/api/assets');
            const data = await response.json();
            const select = document.getElementById('liveAssetSelect');
            if (!data.success || !select) return;
            select.innerHTML = data.assets.map((asset) => `<option value="${asset.symbol}">${asset.symbol} - ${asset.name}</option>`).join('');
            select.value = this.symbol;
        } catch (error) {
            console.warn('Assets indisponiveis', error);
        }
    }

    setupChart() {
        const container = document.getElementById('liveChart');
        if (!container) return;
        container.innerHTML = '';
        this.chart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: Math.max(container.clientHeight, 620),
            layout: {
                background: { type: 'solid', color: '#05070d' },
                textColor: '#cbd5e1',
                fontFamily: 'Inter, Arial, sans-serif',
            },
            grid: {
                horzLines: { color: 'rgba(148, 163, 184, 0.10)' },
                vertLines: { color: 'rgba(148, 163, 184, 0.06)' },
            },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: {
                borderColor: 'rgba(148, 163, 184, 0.25)',
                scaleMargins: { top: 0.08, bottom: 0.24 },
            },
            timeScale: {
                borderColor: 'rgba(148, 163, 184, 0.25)',
                timeVisible: true,
                secondsVisible: false,
            },
        });
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderUpColor: '#26a69a',
            borderDownColor: '#ef5350',
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        });
        this.volumeSeries = this.chart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: '',
            scaleMargins: { top: 0.82, bottom: 0 },
        });
    }

    resizeChart() {
        const container = document.getElementById('liveChart');
        if (!container || !this.chart) return;
        this.chart.applyOptions({ width: container.clientWidth, height: Math.max(container.clientHeight, 620) });
    }

    async resetLive(fit = false) {
        this.statusController?.abort();
        if (this.socket) this.socket.close();
        this.clearPriceLines();
        this.setConnection('Atualizando');
        await this.loadInitialCandles(fit);
        this.fetchLiveStatus('change');
        this.connectStreams();
    }

    async loadInitialCandles(fit = false) {
        try {
            this.setConnection('Carregando');
            const response = await fetch(`/api/candles/${this.symbol}/${this.timeframe}?limit=240`);
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'candles_unavailable');
            const candles = Array.isArray(data.candles) ? data.candles : [];
            const volumes = Array.isArray(data.volumes) ? data.volumes : [];
            this.candleSeries.setData(candles);
            this.volumeSeries.setData(volumes);
            const last = candles[candles.length - 1] || {};
            this.lastCandleTime = Number(last.time || 0);
            this.lastAnalysisPrice = Number(last.close || 0);
            this.setText('livePrice', this.formatPrice(last.close || data.ticker?.lastPrice || 0));
            this.setText('liveChartTitle', `${this.symbol} · ${this.timeframe}`);
            if (fit) this.chart.timeScale().fitContent();
            this.setConnection('Grafico ativo');
        } catch (error) {
            this.setConnection('REST falhou');
            this.pushMessage('Nao foi possivel carregar historico. Tentando manter a tela ativa.');
        }
    }

    connectStreams() {
        const stream = `${this.symbol.toLowerCase()}@kline_${this.timeframe}`;
        const url = `wss://stream.binance.com:9443/ws/${stream}`;
        this.socket = new WebSocket(url);
        this.socket.onopen = () => this.setConnection('Tempo real');
        this.socket.onerror = () => this.setConnection('WebSocket falhou');
        this.socket.onclose = () => {
            this.setConnection('Reconectando');
            setTimeout(() => {
                if (!document.hidden) this.connectStreams();
            }, 2500);
        };
        this.socket.onmessage = (event) => this.handleKline(JSON.parse(event.data));
    }

    handleKline(payload) {
        const kline = payload.k;
        if (!kline) return;
        const candle = {
            time: Math.floor(kline.t / 1000),
            open: Number(kline.o),
            high: Number(kline.h),
            low: Number(kline.l),
            close: Number(kline.c),
        };
        const volume = {
            time: candle.time,
            value: Number(kline.v),
            color: candle.close >= candle.open ? 'rgba(38, 166, 154, 0.45)' : 'rgba(239, 83, 80, 0.45)',
        };
        this.candleSeries.update(candle);
        this.volumeSeries.update(volume);
        this.setText('livePrice', this.formatPrice(candle.close));
        this.lastCandleTime = candle.time;

        const priceMove = this.lastAnalysisPrice ? Math.abs(candle.close - this.lastAnalysisPrice) / this.lastAnalysisPrice : 0;
        const volumeMove = this.lastAnalysisVolume ? Number(kline.v) / Math.max(this.lastAnalysisVolume, 0.00000001) : 1;
        const srBroken = this.isSupportResistanceBroken(candle.close);
        if (kline.x || priceMove >= 0.004 || volumeMove >= 1.8 || srBroken) {
            this.lastAnalysisPrice = candle.close;
            this.lastAnalysisVolume = Number(kline.v);
            this.fetchLiveStatus(kline.x ? 'new_candle' : srBroken ? 'support_resistance_break' : 'strong_change');
        }
    }

    fetchLiveStatus(reason) {
        this.statusController?.abort();
        this.statusController = new AbortController();
        clearTimeout(this.statusTimer);
        this.setStatusLoading(true);
        fetch(`/api/live/status/${this.symbol}/${this.timeframe}?reason=${encodeURIComponent(reason)}`, {
            signal: this.statusController.signal,
        })
            .then((response) => response.json())
            .then((data) => this.updateStatusPanel(data))
            .catch((error) => {
                if (error.name !== 'AbortError') {
                    this.pushMessage('IA recalculando em background. O grafico continua ativo.');
                }
            })
            .finally(() => this.setStatusLoading(false));
        this.statusTimer = setTimeout(() => this.fetchLiveStatus('heartbeat'), 20000);
    }

    updateStatusPanel(data) {
        if (!data) return;
        const state = data.state || 'ANALYZING';
        this.setText('liveOperationalStatus', data.status || 'ANALISANDO');
        this.setText('liveMainMessage', data.message || 'Analisando estrutura do mercado...');
        this.setText('liveScore', Number.isFinite(Number(data.confluence_score)) ? `${data.confluence_score}/100` : '--');
        this.setText('liveConfidence', Number.isFinite(Number(data.confidence)) ? `${data.confidence}%` : '--');
        this.setText('liveDirection', data.probable_direction || '--');
        this.setText('liveTrendStrength', Number.isFinite(Number(data.trend_strength)) ? `${data.trend_strength}%` : '--');
        this.setText('liveVolumeStrength', Number.isFinite(Number(data.volume_strength)) ? `${data.volume_strength}%` : '--');
        this.setText('liveRiskReward', Number.isFinite(Number(data.risk_reward)) ? `1:${Number(data.risk_reward).toFixed(2)}` : '--');
        this.setText('liveAggressiveEntry', this.formatPrice(data.entry_aggressive));
        this.setText('liveConservativeEntry', this.formatPrice(data.entry_conservative));
        this.setText('liveStopLoss', this.formatPrice(data.stop_loss));
        this.setText('liveTakeProfit', this.formatPrice(data.take_profit));
        this.setText('liveReason', data.reason || '--');
        this.setText('liveMarketStatus', data.market_status || '--');
        this.renderMessages(data.messages || []);
        this.renderInvalidations(data.invalidations || []);
        this.supportResistance = data.support_resistance || {};
        this.renderPriceLines(data);
        this.applyStateVisual(state);
        this.handleAlerts(data.alerts || [], data.status || state);
    }

    renderMessages(messages) {
        const feed = document.getElementById('liveMessageFeed');
        if (!feed) return;
        feed.innerHTML = '';
        const items = messages.length ? messages : ['Aguardando leitura da IA...'];
        items.forEach((message) => {
            const row = document.createElement('div');
            row.className = 'live-message-row';
            row.innerHTML = `<i class="fas fa-circle"></i><span>${message}</span>`;
            feed.appendChild(row);
        });
    }

    renderInvalidations(invalidations) {
        const list = document.getElementById('liveInvalidations');
        if (!list) return;
        list.innerHTML = '';
        const items = invalidations.length ? invalidations : ['Sem invalidacao critica no momento.'];
        items.forEach((message) => {
            const row = document.createElement('div');
            row.className = 'live-invalidation-row';
            row.innerHTML = `<i class="fas fa-times-circle"></i><span>${message}</span>`;
            list.appendChild(row);
        });
    }

    renderPriceLines(data) {
        this.clearPriceLines();
        [
            { price: data.entry_aggressive, title: 'Entrada', color: '#38bdf8' },
            { price: data.entry_conservative, title: 'Entrada Cons.', color: '#a78bfa' },
            { price: data.stop_loss, title: 'Stop', color: '#ef4444' },
            { price: data.take_profit, title: 'Take', color: '#22c55e' },
        ].forEach((line) => {
            if (!Number.isFinite(Number(line.price))) return;
            this.priceLines.push(this.candleSeries.createPriceLine({
                price: Number(line.price),
                color: line.color,
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: line.title,
            }));
        });
    }

    clearPriceLines() {
        this.priceLines.forEach((line) => this.candleSeries?.removePriceLine(line));
        this.priceLines = [];
    }

    isSupportResistanceBroken(price) {
        const resistance = Number(this.supportResistance.nearest_resistance);
        const support = Number(this.supportResistance.nearest_support);
        return (Number.isFinite(resistance) && price > resistance) || (Number.isFinite(support) && price < support);
    }

    applyStateVisual(state) {
        const card = document.getElementById('liveStatusCard');
        if (!card) return;
        card.dataset.state = state;
    }

    handleAlerts(alerts, label) {
        const signature = alerts.join('|');
        if (!signature || signature === this.lastAlertSignature) return;
        this.lastAlertSignature = signature;
        this.showToast(label, alerts.includes('HIGH_RISK') || alerts.includes('INVALIDATED') ? 'error' : 'success');
        if (this.soundEnabled) this.playAlertSound(alerts);
    }

    toggleSound() {
        this.soundEnabled = !this.soundEnabled;
        const button = document.getElementById('liveSoundToggle');
        if (button) button.innerHTML = `<i class="fas fa-volume-${this.soundEnabled ? 'up' : 'mute'}"></i>`;
        if (this.soundEnabled) this.playAlertSound([]);
    }

    playAlertSound(alerts) {
        try {
            const context = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = context.createOscillator();
            const gain = context.createGain();
            oscillator.frequency.value = alerts.includes('HIGH_RISK') || alerts.includes('INVALIDATED') ? 220 : 660;
            gain.gain.value = 0.05;
            oscillator.connect(gain);
            gain.connect(context.destination);
            oscillator.start();
            setTimeout(() => {
                oscillator.stop();
                context.close();
            }, 180);
        } catch (error) {
            console.warn('Audio indisponivel', error);
        }
    }

    startCountdown() {
        clearInterval(this.countdownTimer);
        this.countdownTimer = setInterval(() => {
            const seconds = this.timeframeSeconds[this.timeframe] || 900;
            const now = Math.floor(Date.now() / 1000);
            const candleStart = this.lastCandleTime || Math.floor(now / seconds) * seconds;
            const remaining = Math.max(0, candleStart + seconds - now);
            const minutes = Math.floor(remaining / 60);
            const secs = remaining % 60;
            this.setText('liveCountdown', `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`);
        }, 1000);
    }

    setStatusLoading(loading) {
        const card = document.getElementById('liveStatusCard');
        if (card) card.classList.toggle('is-loading', loading);
    }

    setConnection(text) {
        this.setText('liveConnection', text);
    }

    pushMessage(message) {
        const feed = document.getElementById('liveMessageFeed');
        if (!feed) return;
        const row = document.createElement('div');
        row.className = 'live-message-row';
        row.innerHTML = `<i class="fas fa-circle"></i><span>${message}</span>`;
        feed.prepend(row);
        while (feed.children.length > 8) feed.lastElementChild.remove();
    }

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast-notification ${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3200);
    }

    setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value ?? '--';
    }

    formatPrice(price) {
        const value = Number(price);
        if (!Number.isFinite(value) || value === 0) return '--';
        const digits = value >= 1000 ? 2 : value >= 1 ? 4 : 6;
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        }).format(value);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new LiveTradingDashboard();
});
