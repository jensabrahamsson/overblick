/**
 * Minimal radar chart renderer for Big Five personality traits.
 *
 * Pure Canvas implementation - no external dependencies.
 * Displays openness, conscientiousness, extraversion, agreeableness, neuroticism.
 */

class RadarChart {
    constructor(canvas, traits) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.traits = traits;
        this.size = Math.min(canvas.width, canvas.height);
        this.center = { x: this.size / 2, y: this.size / 2 };
        this.radius = this.size * 0.35;

        // Big Five order (clockwise from top)
        this.dimensions = [
            'openness',
            'conscientiousness',
            'extraversion',
            'agreeableness',
            'neuroticism'
        ];

        // Dark theme colors
        this.colors = {
            grid: 'rgba(72, 79, 88, 0.3)',
            fill: 'rgba(63, 185, 80, 0.15)',
            line: 'rgba(63, 185, 80, 0.8)',
            text: '#EAEEF2'
        };
    }

    render() {
        this.ctx.clearRect(0, 0, this.size, this.size);
        this.drawGrid();
        this.drawData();
        this.drawLabels();
    }

    drawGrid() {
        const { ctx, center, radius, dimensions } = this;
        const steps = 5;

        // Draw concentric circles (0.2, 0.4, 0.6, 0.8, 1.0)
        ctx.strokeStyle = this.colors.grid;
        ctx.lineWidth = 1;

        for (let i = 1; i <= steps; i++) {
            const r = (radius * i) / steps;
            ctx.beginPath();
            ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
            ctx.stroke();
        }

        // Draw axes
        const angleStep = (Math.PI * 2) / dimensions.length;
        dimensions.forEach((_, i) => {
            const angle = angleStep * i - Math.PI / 2;
            const x = center.x + radius * Math.cos(angle);
            const y = center.y + radius * Math.sin(angle);

            ctx.beginPath();
            ctx.moveTo(center.x, center.y);
            ctx.lineTo(x, y);
            ctx.stroke();
        });
    }

    drawData() {
        const { ctx, center, radius, dimensions, traits } = this;
        const angleStep = (Math.PI * 2) / dimensions.length;

        // Build polygon points
        const points = dimensions.map((dim, i) => {
            const value = traits[dim] || 0.5; // Default to 0.5 if missing
            const angle = angleStep * i - Math.PI / 2;
            const r = radius * value;
            return {
                x: center.x + r * Math.cos(angle),
                y: center.y + r * Math.sin(angle)
            };
        });

        // Fill polygon
        ctx.fillStyle = this.colors.fill;
        ctx.beginPath();
        points.forEach((p, i) => {
            if (i === 0) ctx.moveTo(p.x, p.y);
            else ctx.lineTo(p.x, p.y);
        });
        ctx.closePath();
        ctx.fill();

        // Stroke polygon
        ctx.strokeStyle = this.colors.line;
        ctx.lineWidth = 2;
        ctx.stroke();

        // Draw data points
        ctx.fillStyle = this.colors.line;
        points.forEach(p => {
            ctx.beginPath();
            ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
            ctx.fill();
        });
    }

    drawLabels() {
        const { ctx, center, radius, dimensions } = this;
        const angleStep = (Math.PI * 2) / dimensions.length;
        const labelRadius = radius * 1.15;

        ctx.fillStyle = this.colors.text;
        ctx.font = '11px ui-monospace, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        dimensions.forEach((dim, i) => {
            const angle = angleStep * i - Math.PI / 2;
            const x = center.x + labelRadius * Math.cos(angle);
            const y = center.y + labelRadius * Math.sin(angle);

            // Abbreviate labels for space
            const label = dim.slice(0, 1).toUpperCase(); // O, C, E, A, N
            ctx.fillText(label, x, y);
        });
    }
}

// Initialize radar charts in a given container (or whole document)
function initRadarCharts(root) {
    root = root || document;
    root.querySelectorAll('canvas[data-radar-chart]').forEach(canvas => {
        if (canvas._radarRendered) return; // skip already-rendered
        const traitsJson = canvas.getAttribute('data-traits');
        if (traitsJson) {
            try {
                const traits = JSON.parse(traitsJson);
                const chart = new RadarChart(canvas, traits);
                chart.render();
                canvas._radarRendered = true;
            } catch (e) {
                console.error('Failed to parse traits for radar chart:', e);
            }
        }
    });
}

// Auto-initialize on page load
document.addEventListener('DOMContentLoaded', () => initRadarCharts());

// Re-initialize after htmx swaps new content into the DOM
document.addEventListener('htmx:afterSettle', (event) => {
    initRadarCharts(event.detail.elt);
});
