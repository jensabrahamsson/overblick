(function() {
    var modal = document.getElementById('chartModal');
    var chartInstance = null;

    window.openMarketChart = function(marketId, question) {
        modal.style.display = 'flex';
        document.getElementById('chartModalTitle').textContent = question || 'Price Chart';
        document.getElementById('chartInfo').textContent = 'Loading...';

        fetch('/api/polymarket/chart/' + encodeURIComponent(marketId))
            .then(function(r) { return r.json(); })
            .then(function(data) { renderChart(data); })
            .catch(function() {
                document.getElementById('chartInfo').textContent = 'No price data yet.';
            });
    };

    function renderChart(data) {
        var container = document.getElementById('chartContainer');
        while (container.firstChild) container.removeChild(container.firstChild);
        if (chartInstance) { chartInstance.remove(); chartInstance = null; }

        if (!data.prices || data.prices.length === 0) {
            document.getElementById('chartInfo').textContent = 'No price history yet. Collecting data every scan cycle.';
            return;
        }

        chartInstance = LightweightCharts.createChart(container, {
            layout: { background: { type: 'solid', color: '#0d1117' }, textColor: '#9ba4ae', fontSize: 11 },
            grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
            crosshair: { mode: 0 },
            rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)', scaleMargins: { top: 0.1, bottom: 0.1 } },
            timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true, secondsVisible: false },
            width: container.clientWidth, height: container.clientHeight || 400,
        });

        var yesSeries = chartInstance.addLineSeries({
            color: '#3fb950', lineWidth: 2, title: 'YES',
            priceFormat: { type: 'custom', formatter: function(p) { return (p*100).toFixed(0) + '\u00A2'; } },
        });
        yesSeries.setData(data.prices.map(function(p) { return { time: p.time, value: p.yes }; }));

        var noSeries = chartInstance.addLineSeries({ color: '#f97583', lineWidth: 1, lineStyle: 2, title: 'NO' });
        noSeries.setData(data.prices.map(function(p) { return { time: p.time, value: p.no }; }));

        if (data.trades && data.trades.length > 0) {
            yesSeries.setMarkers(data.trades.map(function(t) {
                var isBuy = t.action.indexOf('BUY') >= 0;
                return {
                    time: t.time, position: isBuy ? 'belowBar' : 'aboveBar',
                    color: isBuy ? '#3fb950' : '#f97583',
                    shape: isBuy ? 'arrowUp' : 'arrowDown',
                    text: t.action + ' $' + t.size.toFixed(0),
                };
            }));
        }

        chartInstance.timeScale().fitContent();
        var latest = data.prices[data.prices.length - 1];
        document.getElementById('chartInfo').textContent =
            'YES: ' + (latest.yes*100).toFixed(0) + '\u00A2 | NO: ' + (latest.no*100).toFixed(0) + '\u00A2 | ' +
            data.prices.length + ' points' + (data.trades.length > 0 ? ' | ' + data.trades.length + ' trades' : '');
    }

    document.addEventListener('click', function(e) {
        if (e.target.closest('[data-action="close-chart"]') || e.target.classList.contains('chart-modal-backdrop')) {
            modal.style.display = 'none';
            if (chartInstance) { chartInstance.remove(); chartInstance = null; }
        }
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && modal.style.display !== 'none') {
            modal.style.display = 'none';
            if (chartInstance) { chartInstance.remove(); chartInstance = null; }
        }
    });
    window.addEventListener('resize', function() {
        if (chartInstance) {
            var c = document.getElementById('chartContainer');
            chartInstance.resize(c.clientWidth, c.clientHeight || 400);
        }
    });
})();