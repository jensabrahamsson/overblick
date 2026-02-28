/**
 * Minimal Canvas sparkline renderer for observability dashboard.
 *
 * Reads JSON data from `data-sparkline` attribute on <canvas> elements.
 * Expected format: [{"total": N, "failures": M}, ...]
 *
 * Line color: #58a6ff (accent blue), fill: rgba(88,166,255,0.1)
 * Red dots mark hours with failures.
 */

class SparklineChart {
    constructor(canvas, data) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.data = data;

        // Use CSS dimensions for crisp rendering
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
        this.width = rect.width;
        this.height = rect.height;
    }

    render() {
        const { ctx, data, width, height } = this;
        if (!data || data.length < 2) return;

        const padding = { top: 4, bottom: 4, left: 2, right: 2 };
        const w = width - padding.left - padding.right;
        const h = height - padding.top - padding.bottom;

        const maxVal = Math.max(1, ...data.map(d => d.total || 0));
        const points = data.map((d, i) => ({
            x: padding.left + (i / (data.length - 1)) * w,
            y: padding.top + h - ((d.total || 0) / maxVal) * h,
            failures: d.failures || 0,
        }));

        // Fill area
        ctx.beginPath();
        ctx.moveTo(points[0].x, height - padding.bottom);
        points.forEach(p => ctx.lineTo(p.x, p.y));
        ctx.lineTo(points[points.length - 1].x, height - padding.bottom);
        ctx.closePath();
        ctx.fillStyle = 'rgba(88, 166, 255, 0.1)';
        ctx.fill();

        // Line
        ctx.beginPath();
        points.forEach((p, i) => {
            if (i === 0) ctx.moveTo(p.x, p.y);
            else ctx.lineTo(p.x, p.y);
        });
        ctx.strokeStyle = '#58a6ff';
        ctx.lineWidth = 1.5;
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Red dots for hours with failures
        points.forEach(p => {
            if (p.failures > 0) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
                ctx.fillStyle = '#f97583';
                ctx.fill();
            }
        });
    }
}

function initSparklines(root) {
    root = root || document;
    root.querySelectorAll('canvas[data-sparkline]').forEach(canvas => {
        if (canvas._sparklineRendered) return;
        const json = canvas.getAttribute('data-sparkline');
        if (json) {
            try {
                const data = JSON.parse(json);
                const chart = new SparklineChart(canvas, data);
                chart.render();
                canvas._sparklineRendered = true;
            } catch (e) {
                console.error('Sparkline parse error:', e);
            }
        }
    });
}

document.addEventListener('DOMContentLoaded', () => initSparklines());
document.addEventListener('htmx:afterSettle', (event) => {
    // Reset rendered flags on swapped canvases so fresh data gets rendered
    const root = event.detail.elt || document;
    root.querySelectorAll('canvas[data-sparkline]').forEach(c => {
        c._sparklineRendered = false;
    });
    initSparklines(root);
});
