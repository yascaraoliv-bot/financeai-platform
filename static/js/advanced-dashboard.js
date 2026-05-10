class AdvancedDashboard {
    constructor() {
        this.currentAsset = 'BTCUSDT';
        this.currentTimeframe = '1h';
        this.chart = null;
        this.candleSeries = null;
        this.volumeSeries = null;
        this.overlaySeries = {};
        this.priceLines = [];
        this.refreshTimer = null;
        this.klineSocket = null;
        this.isLoading = false;
        this.refreshMs = 10000;
        this.init();
    }

    async init() {
        this.setupChart();
        this.setupEventListeners();
        await this.loadAssets();
        await this.updateDashboard();
        this.startRealtime();
    }

    setupEventListeners() {
        const assetSelect = document.getElementById('assetSelect');
        assetSelect?.addEventListener('change', (event) => {
            this.currentAsset = event.target.value;
            this.updateDashboard();
        });

        document.querySelectorAll('.tf-btn').forEach((button) => {
            button.addEventListener('click', (event) => {
                document.querySelectorAll('.tf-btn').forEach((btn) => btn.classList.remove('active'));
                event.currentTarget.classList.add('active');
                this.currentTimeframe = event.currentTarget.dataset.tf;
                this.updateDashboard(true);
            });
        });

        document.getElementById('btnRunBacktest')?.addEventListener('click', () => this.runBacktest());
        document.querySelector('[data-chart-action="fit"]')?.addEventListener('click', () => this.chart?.timeScale().fitContent());
        window.addEventListener('resize', () => this.resizeChart());
    }

    async loadAssets() {
        const response = await fetch('/api/assets');
        const data = await response.json();
        const select = document.getElementById('assetSelect');
        if (!data.success || !select) return;

        select.innerHTML = data.assets.map((asset) => (
            `<option value="${asset.symbol}">${asset.symbol} - ${asset.name}</option>`
        )).join('');
        select.value = this.currentAsset;
    }

    setupChart() {
        const container = document.getElementById('chart-container');
        if (!container) return;
        container.innerHTML = '';

        this.chart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: Math.max(container.clientHeight, 520),
            layout: {
                textColor: '#b6c2d9',
                background: { type: 'solid', color: '#0b0f1a' },
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
            watermark: {
                visible: true,
                text: 'BINANCE REAL-TIME',
                color: 'rgba(148, 163, 184, 0.12)',
                fontSize: 18,
                horzAlign: 'right',
                vertAlign: 'bottom',
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

        this.overlaySeries = {
            ema9: this.chart.addLineSeries({ color: '#facc15', lineWidth: 2, title: 'EMA 9' }),
            ema21: this.chart.addLineSeries({ color: '#38bdf8', lineWidth: 2, title: 'EMA 21' }),
            ema200: this.chart.addLineSeries({ color: '#f97316', lineWidth: 2, title: 'EMA 200' }),
            vwap: this.chart.addLineSeries({ color: '#a78bfa', lineWidth: 2, title: 'VWAP' }),
            bollinger_upper: this.chart.addLineSeries({ color: 'rgba(148, 163, 184, 0.8)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted }),
            bollinger_middle: this.chart.addLineSeries({ color: 'rgba(148, 163, 184, 0.45)', lineWidth: 1 }),
            bollinger_lower: this.chart.addLineSeries({ color: 'rgba(148, 163, 184, 0.8)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted }),
        };
    }

    resizeChart() {
        const container = document.getElementById('chart-container');
        if (!container || !this.chart) return;
        this.chart.applyOptions({
            width: container.clientWidth,
            height: Math.max(container.clientHeight, 520),
        });
    }

    startRealtime() {
        clearInterval(this.refreshTimer);
        this.refreshTimer = setInterval(() => this.updateDashboard(false), this.refreshMs);
    }

    async updateDashboard(fit = false) {
        if (this.isLoading) return;
        this.isLoading = true;
        this.setConnectionState('Atualizando');

        try {
            const [candlesResponse, analysisResponse] = await Promise.all([
                fetch(`/api/candles/${this.currentAsset}/${this.currentTimeframe}?limit=600`),
                fetch(`/api/analysis/${this.currentAsset}/${this.currentTimeframe}`),
            ]);
            const candles = await candlesResponse.json();
            const analysis = await analysisResponse.json();

            if (!candles.success) throw new Error(candles.error || 'Erro nos candles');
            if (!analysis.success) throw new Error(analysis.error || 'Erro na analise');

            this.updateChart(candles, analysis, fit);
            this.updateAnalysisPanel(analysis, candles);
            this.connectKlineStream();
            await Promise.all([this.updateMultiTimeframe(), this.updateHeatmap()]);
            this.setConnectionState('Tempo real');
        } catch (error) {
            console.error(error);
            this.setConnectionState('Falha');
            this.showNotification(`Erro ao atualizar: ${error.message}`, 'error');
        } finally {
            this.isLoading = false;
        }
    }

    updateChart(candles, analysis, fit = false) {
        this.candleSeries.setData(candles.candles);
        this.volumeSeries.setData(candles.volumes);
        Object.entries(candles.overlays || {}).forEach(([key, values]) => {
            this.overlaySeries[key]?.setData(values);
        });

        this.clearPriceLines();
        this.addTradePriceLines(analysis.levels);
        this.candleSeries.setMarkers(analysis.markers || []);
        if (fit) this.chart.timeScale().fitContent();
    }

    connectKlineStream() {
        const stream = `${this.currentAsset.toLowerCase()}@kline_${this.currentTimeframe}`;
        const url = `wss://stream.binance.com:9443/ws/${stream}`;
        if (this.klineSocket?.url === url && this.klineSocket.readyState <= 1) return;
        if (this.klineSocket) this.klineSocket.close();

        this.klineSocket = new WebSocket(url);
        this.klineSocket.onopen = () => this.setConnectionState('WebSocket');
        this.klineSocket.onmessage = (event) => {
            const payload = JSON.parse(event.data);
            const kline = payload.k;
            if (!kline) return;
            const candle = {
                time: Math.floor(kline.t / 1000),
                open: Number(kline.o),
                high: Number(kline.h),
                low: Number(kline.l),
                close: Number(kline.c),
            };
            this.candleSeries.update(candle);
            this.volumeSeries.update({
                time: candle.time,
                value: Number(kline.v),
                color: candle.close >= candle.open ? 'rgba(38, 166, 154, 0.45)' : 'rgba(239, 83, 80, 0.45)',
            });
            this.setText('currentPrice', this.formatPrice(candle.close));
            this.setText('lastUpdate', new Date().toLocaleTimeString('pt-BR'));
        };
        this.klineSocket.onerror = () => this.setConnectionState('REST ativo');
        this.klineSocket.onclose = () => {
            if (!document.hidden) this.setConnectionState('Reconectando');
        };
    }

    clearPriceLines() {
        this.priceLines.forEach((line) => this.candleSeries.removePriceLine(line));
        this.priceLines = [];
    }

    addTradePriceLines(levels) {
        const lines = [
            { key: 'entrada', title: 'Entrada', color: '#38bdf8' },
            { key: 'stop_loss', title: 'Stop', color: '#ef4444' },
            { key: 'alvo_1', title: 'Take 1', color: '#22c55e' },
            { key: 'alvo_2', title: 'Take 2', color: '#16a34a' },
        ];
        lines.forEach((line) => {
            if (!Number.isFinite(levels[line.key])) return;
            const priceLine = this.candleSeries.createPriceLine({
                price: levels[line.key],
                color: line.color,
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: line.title,
            });
            this.priceLines.push(priceLine);
        });
    }

    updateAnalysisPanel(analysis, candlesPayload) {
        const signal = analysis.signal;
        const indicators = signal.indicators;
        const ticker = analysis.ticker || candlesPayload.ticker || {};
        const score = analysis.operational_score;

        this.setText('chartTitle', `${analysis.symbol} · Binance · ${analysis.timeframe}`);
        this.setText('currentPrice', this.formatPrice(analysis.current_price));
        this.setText('priceChange', `${Number(ticker.priceChangePercent || analysis.price_change || 0).toFixed(2)}%`);
        this.setText('operationalScore', `${score}/100`);
        this.setText('scoreValue', Math.round(score));
        this.setText('scoreText', score > 70 ? 'Alta qualidade' : score > 50 ? 'Aguardando confirmacao' : 'Risco elevado');
        this.updateGauge(score);

        this.setText('mainSignal', this.getSignalText(signal.signal_type));
        this.setText('confidenceValue', `${Math.round(signal.confidence)}%`);

        this.setText('levelEntrada', this.formatPrice(analysis.levels.entrada));
        this.setText('levelStop', this.formatPrice(analysis.levels.stop_loss));
        this.setText('levelTarget1', this.formatPrice(analysis.levels.alvo_1));
        this.setText('levelTarget2', this.formatPrice(analysis.levels.alvo_2));
        this.setText('riskRatio', `1:${analysis.levels.risco_retorno.toFixed(2)}`);

        this.setText('rsiValue', indicators.rsi.toFixed(2));
        this.setText('macdValue', indicators.macd.toFixed(4));
        this.setText('atrValue', indicators.atr.toFixed(2));
        this.setText('volumeValue', this.formatCompact(indicators.volume));
        this.setText('vwapValue', this.formatPrice(indicators.vwap));
        this.setText('emaValue', `${this.formatPrice(indicators.ema9)} / ${this.formatPrice(indicators.ema21)}`);
        this.setText('bbValue', `${this.formatPrice(indicators.bollinger_lower)} - ${this.formatPrice(indicators.bollinger_upper)}`);

        this.updateReasoning(analysis.reasoning);
        this.updateSupportResistance(analysis.support_resistance, analysis.current_price);
        this.updateCandleReading(analysis.candle_reading);
    }

    updateGauge(score) {
        const gauge = document.querySelector('.gauge-fill');
        if (!gauge) return;
        const circumference = 282;
        const filled = Math.max(0, Math.min(100, score)) / 100 * circumference;
        gauge.setAttribute('stroke-dasharray', `${filled} ${circumference}`);
        gauge.style.stroke = score > 70 ? '#22c55e' : score > 50 ? '#facc15' : '#ef4444';
    }

    updateReasoning(reasoning) {
        const list = document.getElementById('reasoningList');
        if (!list) return;
        list.innerHTML = '';
        reasoning.forEach((text) => {
            const item = document.createElement('div');
            item.className = 'reasoning-item';
            item.innerHTML = `<i class="fas fa-check-circle"></i><span>${text}</span>`;
            list.appendChild(item);
        });
    }

    updateSupportResistance(levels, currentPrice) {
        const list = document.getElementById('srList');
        if (!list) return;
        list.innerHTML = '';
        levels.forEach((level, index) => {
            const distance = ((level.price - currentPrice) / currentPrice * 100).toFixed(2);
            const item = document.createElement('div');
            item.className = `sr-item ${level.type}`;
            item.innerHTML = `
                <span class="sr-label">${level.type === 'resistance' ? 'Resistencia' : 'Suporte'} ${index + 1}</span>
                <span class="sr-price">${this.formatPrice(level.price)}</span>
                <span class="sr-distance">${distance}%</span>
            `;
            list.appendChild(item);
        });
    }

    updateCandleReading(candles) {
        const list = document.getElementById('candleReadingList');
        if (!list) return;
        list.innerHTML = '';
        candles.forEach((candle) => {
            const row = document.createElement('div');
            row.className = `candle-row ${candle.direction}`;
            row.innerHTML = `
                <span>${candle.direction.toUpperCase()}</span>
                <span>${this.formatPrice(candle.close)}</span>
                <span>${candle.body_pct.toFixed(2)}%</span>
            `;
            list.appendChild(row);
        });
    }

    async updateMultiTimeframe() {
        const response = await fetch(`/api/multi-timeframe/${this.currentAsset}`);
        const data = await response.json();
        if (!data.success) return;
        Object.entries(data.analysis).forEach(([timeframe, item]) => {
            this.setText(`tf${timeframe.replace(/\W/g, '')}`, this.getSignalText(item.signal, true));
        });
    }

    async updateHeatmap() {
        const response = await fetch('/api/heatmap');
        const data = await response.json();
        if (!data.success) return;
        this.renderHeatmap(data.heatmap);
    }

    renderHeatmap(heatmap) {
        const container = document.querySelector('.heatmap-grid');
        if (!container) return;
        container.innerHTML = '';

        Object.entries(heatmap).forEach(([asset, timeframes]) => {
            const row = document.createElement('div');
            row.className = 'heatmap-row';
            row.innerHTML = `<div class="heatmap-asset">${asset}</div>`;
            Object.entries(timeframes).forEach(([tf, item]) => {
                const cell = document.createElement('button');
                cell.className = 'heatmap-cell';
                cell.style.backgroundColor = item.color;
                cell.innerHTML = `<span>${tf}</span><strong>${item.confidence}%</strong>`;
                cell.title = `${asset} ${tf} · ${item.signal}`;
                cell.addEventListener('click', () => {
                    this.currentAsset = asset;
                    this.currentTimeframe = tf;
                    document.getElementById('assetSelect').value = asset;
                    document.querySelectorAll('.tf-btn').forEach((btn) => {
                        btn.classList.toggle('active', btn.dataset.tf === tf);
                    });
                    this.updateDashboard(true);
                });
                row.appendChild(cell);
            });
            container.appendChild(row);
        });
    }

    async runBacktest() {
        const button = document.getElementById('btnRunBacktest');
        if (button) {
            button.textContent = 'Executando...';
            button.disabled = true;
        }
        try {
            const response = await fetch(`/api/backtest/${this.currentAsset}`);
            const data = await response.json();
            if (!data.success) throw new Error(data.error || 'Erro no backtest');
            const result = data.backtest;
            this.setText('totalTrades', result.total_trades);
            this.setText('winRate', `${result.win_rate.toFixed(1)}%`);
            this.setText('totalReturn', `${result.total_return.toFixed(2)}%`);
            this.setText('profitFactor', Number(result.profit_factor).toFixed(2));
            this.showNotification('Backtest atualizado com dados reais da Binance.', 'success');
        } catch (error) {
            this.showNotification(error.message, 'error');
        } finally {
            if (button) {
                button.textContent = 'Executar Backtest';
                button.disabled = false;
            }
        }
    }

    setConnectionState(text) {
        this.setText('connectionState', text);
        this.setText('lastUpdate', new Date().toLocaleTimeString('pt-BR'));
    }

    setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    getSignalText(signal, compact = false) {
        const map = {
            entrada_agressiva: compact ? 'COMPRA+' : 'COMPRA AGRESSIVA',
            entrada_conservadora: compact ? 'COMPRA C.' : 'COMPRA CONSERVADORA',
            compra: 'COMPRA',
            venda_agressiva: compact ? 'VENDA+' : 'VENDA AGRESSIVA',
            venda: 'VENDA',
            neutro: 'NEUTRO',
        };
        return map[signal] || signal;
    }

    formatPrice(price) {
        const value = Number(price);
        if (!Number.isFinite(value)) return '--';
        const digits = value >= 1000 ? 2 : value >= 1 ? 4 : 6;
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        }).format(value);
    }

    formatCompact(value) {
        return new Intl.NumberFormat('en-US', {
            notation: 'compact',
            maximumFractionDigits: 2,
        }).format(Number(value || 0));
    }

    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `toast-notification ${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);
        setTimeout(() => notification.remove(), 3200);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new AdvancedDashboard();
});
