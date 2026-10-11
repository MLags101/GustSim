"""Local PDF simulation report (A4), built with matplotlib only.

Structure follows established aerodynamic reports: cover, project details, model views,
performance (forces in both frames, convergence, coefficients, force and power against speed),
drag analysis, flow analysis, insights, quality and status, and a reproducibility appendix.
Every page carries the evidence states; nothing in the report claims validation.
"""
import json
import textwrap
import time
from pathlib import Path
import numpy as np

A4 = (8.27, 11.69)
INK, MUTED, ACCENT, INDIGO, LINE = '#1f2933', '#5f6b7a', '#02a6d6', '#4053c5', '#d5dbe3'
TYPICAL_CD = [('Streamlined body', 0.04), ('Sports car', 0.30), ('Family car', 0.33), ('SUV', 0.40),
              ('Truck', 0.60), ('Upright cyclist', 0.90), ('Cube', 1.05), ('Flat plate', 1.28)]


def fmt(value, unit='', digits=4):
    if value is None:
        return '—'
    if isinstance(value, float):
        text = f'{value:.{digits}g}'
    else:
        text = str(value)
    return f'{text} {unit}'.strip()


class Report:
    def __init__(self, run, spec, meta, case):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        self.plt, self.PdfPages = plt, PdfPages
        self.run, self.spec, self.meta, self.case = run, spec, meta, Path(case)
        self.result = run['result']
        self.read = lambda name: json.loads((self.case/name).read_text(encoding='utf-8')) if (self.case/name).is_file() else None
        self.forces = self.read('results/forces.json')
        self.insights = self.read('results/insights.json')
        self.convergence = self.read('results/convergence.json')
        self.manifest = self.read('results/package/manifest.json')
        self.transient = self.read('results/transient.json') if spec.time.mode == 'transient' else None
        self.page_number = 0
        self.contents = []

    # ---- page furniture ----------------------------------------------------------------
    def status_line(self):
        status = (self.manifest or {}).get('status') or {}
        return (f"Execution: {self.run['status']} · Numerical quality: {self.result.get('numerical_status', 'unavailable')} · "
                f"Experimental validation: pending · Physics: {status.get('physics_maturity', 'core')}")

    def page(self, pdf, title, draw):
        figure = self.plt.figure(figsize=A4)
        self.page_number += 1
        self.contents.append((title, self.page_number))
        figure.text(0.07, 0.955, title, fontsize=17, color=INK, weight='bold')
        figure.add_artist(self.plt.Line2D([0.07, 0.93], [0.945, 0.945], color=ACCENT, linewidth=2))
        figure.text(0.07, 0.03, self.status_line(), fontsize=6.5, color=MUTED)
        figure.text(0.93, 0.03, f'{self.page_number}', fontsize=8, color=MUTED, ha='right')
        figure.text(0.07, 0.015, f"GustSim · run {self.run['id']} · {self.spec.name}", fontsize=6.5, color=MUTED)
        draw(figure)
        pdf.savefig(figure)
        self.plt.close(figure)

    def paragraph(self, figure, x, y, text, width=95, size=9, color=INK, spacing=0.016):
        for line in textwrap.wrap(text, width):
            figure.text(x, y, line, fontsize=size, color=color)
            y -= spacing
        return y - spacing*0.5

    def table(self, figure, rect, rows, columns, widths=None):
        axes = figure.add_axes(rect); axes.axis('off')
        table = axes.table(cellText=rows, colLabels=columns, loc='upper left', cellLoc='left', colLoc='left', colWidths=widths)
        table.auto_set_font_size(False); table.set_fontsize(8); table.scale(1, 1.35)
        for (r, c), cell in table.get_celld().items():
            cell.set_edgecolor(LINE)
            if r == 0:
                cell.set_text_props(weight='bold', color=INK); cell.set_facecolor('#eef5f9')
        return axes

    def image(self, figure, rect, relative, caption=None):
        path = self.case/'results/package'/relative if not str(relative).startswith('results') else self.case/relative
        axes = figure.add_axes(rect); axes.axis('off')
        if path.is_file():
            axes.imshow(self.plt.imread(str(path)))
        else:
            axes.text(0.5, 0.5, 'Render unavailable', ha='center', va='center', color=MUTED, fontsize=9)
            axes.add_patch(self.plt.Rectangle((0, 0), 1, 1, transform=axes.transAxes, fill=False, edgecolor=LINE))
        if caption:
            figure.text(rect[0], rect[1]-0.012, caption, fontsize=7.5, color=MUTED)

    # ---- pages -------------------------------------------------------------------------
    def cover(self, figure):
        figure.patches.append(self.plt.Rectangle((0, 0.62), 1, 0.38, transform=figure.transFigure, color='#0b2a4a', zorder=-1))
        figure.patches.append(self.plt.Rectangle((0, 0.62), 1, 0.012, transform=figure.transFigure, color=ACCENT, zorder=-1))
        figure.texts.clear()
        figure.text(0.08, 0.86, 'Aerodynamic Simulation Report' if self.spec.mode != 'internal' else 'Flow Simulation Report',
                    fontsize=24, color='white', weight='bold')
        figure.text(0.08, 0.82, self.spec.name, fontsize=14, color='#cfe9f5')
        figure.text(0.08, 0.76, time.strftime('%d %B %Y', time.localtime(self.run.get('finished') or self.run['updated'])), fontsize=11, color='#cfe9f5')
        figure.text(0.08, 0.73, f"Accuracy: {self.spec.mesh.tier.capitalize()} · {fmt(self.result.get('cell_count'), 'cells')}", fontsize=11, color='#cfe9f5')
        self.image(figure, [0.08, 0.2, 0.84, 0.38], 'renders/iso_cp.png')
        kind = ('Transient (time-accurate, experimental) results are averaged over the averaging window and reported with their fluctuations. '
                if self.transient else 'Steady Reynolds-averaged results describe time-averaged flow. ')
        y = self.paragraph(figure, 0.08, 0.15, 'Computed with OpenFOAM (OpenCFD v2606) on this computer by GustSim. ' + kind +
                           'They have not been validated against experiments for this geometry; physical testing remains necessary '
                           'for safety-critical decisions.', size=8.5, color=MUTED)
        figure.text(0.08, 0.06, self.status_line(), fontsize=7.5, color=MUTED)

    def details(self, figure):
        spec, meta = self.spec, self.meta
        forces = self.forces or {}
        rows = [['Model', meta.get('filename', '—')], ['Geometry', meta.get('geometry_fidelity') or 'imported CAD'],
                ['Motion', f"{spec.flow.speed:.4g} m/s ({spec.flow.speed*3.6:.4g} km/h)"],
                ['Fluid', f"{spec.fluid.name}, ρ = {spec.fluid.density:.4g} kg/m³, μ = {spec.fluid.dynamic_viscosity:.4g} Pa·s"],
                ['Ground', spec.environment.ground],
                ['Turbulence model', ('k–ω SST (unsteady RANS)' if spec.flow.turbulence != 'laminar' else 'Laminar (time-accurate)') if self.transient
                 else ('k–ω SST (RANS, steady)' if spec.flow.turbulence != 'laminar' else 'Laminar (steady)')],
                ['Accuracy tier', spec.mesh.tier], ['Cells', fmt(self.result.get('cell_count'))],
                ['Reference area', fmt(spec.references.area, 'm²')], ['Reference length', fmt(spec.references.length, 'm')],
                ['Frontal area', fmt(forces.get('frontal_area_m2'), 'm²')],
                ['Blockage', f"{forces['blockage']['ratio']*100:.2f} %" if forces.get('blockage') else '—'],
                ['Solver', 'OpenFOAM simpleFoam, then pimpleFoam (OpenCFD v2606)' if self.transient else 'OpenFOAM simpleFoam (OpenCFD v2606)'],
                ['Convergence', (self.transient or {}).get('message') or (self.convergence or {}).get('message', '—')]]
        self.table(figure, [0.07, 0.45, 0.86, 0.47], rows, ['Project detail', 'Value'], [0.3, 0.7])
        self.paragraph(figure, 0.07, 0.4, 'Disclaimer. This report presents a numerical solution of a mathematical model. '
                       'Numerical checks (convergence, mesh quality) are reported separately from experimental validation, '
                       'which is pending for this configuration. Use physical testing for safety-critical decisions.', size=8, color=MUTED)

    def model(self, figure):
        low, high = np.asarray(self.meta['bounds'])
        size = high - low
        self.paragraph(figure, 0.07, 0.91, f'Bounding box: X {size[0]:.4g} m × Y {size[1]:.4g} m × Z {size[2]:.4g} m. '
                       f'The flow direction is {np.round(np.asarray(self.spec.flow.velocity())/max(self.spec.flow.speed, 1e-12), 3).tolist()}.')
        positions = [[0.07, 0.5, 0.42, 0.33], [0.51, 0.5, 0.42, 0.33], [0.07, 0.12, 0.42, 0.33], [0.51, 0.12, 0.42, 0.33]]
        for rect, (view, caption) in zip(positions, [('iso', '3D view'), ('front', 'Front view'), ('top', 'Top view'), ('side', 'Side view')]):
            self.image(figure, rect, f'renders/{view}_cp.png', caption + ' · surface pressure coefficient Cp')

    def performance(self, figure):
        forces = self.forces
        if not forces:
            self.paragraph(figure, 0.07, 0.9, 'Force tables are unavailable for this run.')
            return
        def rows(frame, labels):
            block = forces['frames'][frame]['total']
            return [[label, fmt(block['pressure_n'][key], 'N'), fmt(block['viscous_n'][key], 'N'), fmt(block['force_n'][key], 'N'),
                     fmt(block['force_n'][key]/9.80665, 'kgf')] for key, label in labels]
        self.table(figure, [0.07, 0.75, 0.86, 0.17], rows('wind', [('drag', 'Drag (Fx)'), ('side', 'Side (Fy)'), ('lift', 'Lift (Fz)')]),
                   ['Wind-tunnel frame', 'Pressure', 'Friction', 'Total', 'Total'])
        self.table(figure, [0.07, 0.58, 0.86, 0.17], rows('object', [('x', 'X'), ('y', 'Y'), ('z', 'Z')]),
                   ['Object frame', 'Pressure', 'Friction', 'Total', 'Total'])
        c = forces['coefficients']
        coefficient_rows = [['Cd', fmt(c['cd'])], ['CdA', fmt(c['cda_m2'], 'm²')], ['Cl', fmt(c['cl'])], ['Cl front', fmt(c['cl_front'])],
                            ['Cl rear', fmt(c['cl_rear'])], ['L/D', fmt(c['l_over_d'])]]
        self.table(figure, [0.07, 0.36, 0.38, 0.2], coefficient_rows, ['Coefficient', 'Value'])
        averaging = forces['averaging']
        window = (self.transient or {}).get('window')
        span = (f"the transient window t = {fmt(window['start_s'])}–{fmt(window['end_s'])} s" if window else
                f"iterations {fmt(averaging['start'])}–{fmt(averaging['end'])} ({averaging['basis'].replace('_', ' ')})")
        self.paragraph(figure, 0.5, 0.55, f"Loads are averaged over {span}. Coefficients use the reference area "
                       f"{fmt(forces['reference']['area_m2'], 'm²')} and dynamic pressure {fmt(forces['reference']['dynamic_pressure_pa'], 'Pa')}.",
                       width=48, size=8)
        if c.get('cd') is not None:
            axes = figure.add_axes([0.3, 0.08, 0.63, 0.22])
            names = [n for n, _ in TYPICAL_CD]; values = [v for _, v in TYPICAL_CD]
            axes.barh(names, values, color='#c9d6e3')
            axes.axvline(c['cd'], color=ACCENT, linewidth=2.5)
            axes.text(c['cd'], len(names)-0.4, f'  This model {c["cd"]:.3f}', color=ACCENT, fontsize=8, va='center')
            axes.set_xlim(0, 1.5); axes.tick_params(labelsize=7); axes.set_title('Cd on a scale of typical values (indicative, not GustSim results)', fontsize=8, color=MUTED)

    def convergence_page(self, figure):
        from .monitor import history
        try:
            forces, residuals = history(self.case, self.spec)
        except Exception:
            forces, residuals = [], []
        if self.transient:
            # This page covers the steady initialisation; the transient stage has its own page.
            start = self.transient['plan']['start']
            forces = [f for f in forces if f['iteration'] <= start]
            residuals = [r for r in residuals if r['iteration'] <= start]
        self.paragraph(figure, 0.07, 0.915, (self.convergence or {}).get('message', 'No convergence record is available for this run.'), size=9)
        axes = figure.add_axes([0.12, 0.52, 0.8, 0.34])
        if forces:
            for key, colour in (('drag_n', INDIGO), ('lift_n', ACCENT), ('thrust_n', INDIGO), ('torque_nm', ACCENT), ('force_n', INDIGO)):
                if key in forces[0]:
                    axes.plot([f['iteration'] for f in forces], [f.get(key) for f in forces], color=colour, linewidth=1, label=key.replace('_', ' '))
            averaging = (self.convergence or {}).get('averaging')
            if averaging:
                axes.axvspan(averaging['start'], averaging['end'], color=ACCENT, alpha=0.15, label='Averaging window')
            axes.legend(fontsize=7, frameon=False)
        else:
            axes.text(0.5, 0.5, 'No load history', ha='center', color=MUTED, transform=axes.transAxes)
        axes.set_xlabel('Iteration (not physical time)', fontsize=8); axes.set_ylabel('Load [N or N·m]', fontsize=8); axes.tick_params(labelsize=7)
        axes.grid(color=LINE, linewidth=0.5)
        axes = figure.add_axes([0.12, 0.1, 0.8, 0.34])
        fields = sorted({k for r in residuals for k in r if k != 'iteration'})
        for field in fields:
            points = [(r['iteration'], r[field]) for r in residuals if r.get(field)]
            if points:
                axes.semilogy(*zip(*points), linewidth=0.9, label=field)
        if fields:
            axes.legend(fontsize=7, frameon=False, ncol=3)
        else:
            axes.text(0.5, 0.5, 'No residual history', ha='center', color=MUTED, transform=axes.transAxes)
        axes.set_xlabel('Iteration', fontsize=8); axes.set_ylabel('Initial residual', fontsize=8); axes.tick_params(labelsize=7)
        axes.grid(color=LINE, linewidth=0.5)

    def transient_page(self, figure):
        stats = self.transient
        plan = stats['plan']
        statistics = stats.get('statistics') or {}

        def coefficient(key):
            s = statistics.get(key)
            return f"{s['mean']:.4g} ± {s['std']:.2g}" if s else '—'
        rows = [['Verdict', stats.get('message', '—')],
                ['Simulated time', f"{fmt(plan['duration_s'], 's')} ({plan['flow_throughs']:.3g} domain flow-throughs)"],
                ['Averaging window', f"{fmt(stats['window']['start_s'], 's')} – {fmt(stats['window']['end_s'], 's')}" if stats.get('window') else '—'],
                ['Cd (mean ± std)', coefficient('cd')], ['Cl (mean ± std)', coefficient('cl')],
                ['Dominant frequency', fmt(stats.get('dominant_frequency_hz'), 'Hz')], ['Strouhal number St = f L / U', fmt(stats.get('strouhal'))],
                ['Periods in the window', fmt(stats.get('periods_in_window'))], ['Maximum Courant number (limit)', fmt(plan['max_courant'])]]
        self.table(figure, [0.07, 0.66, 0.86, 0.26], [[a, textwrap.shorten(str(b), 80)] for a, b in rows], ['Transient result', 'Value'], [0.35, 0.65])
        history = stats.get('history')
        axes = figure.add_axes([0.12, 0.36, 0.8, 0.24])
        if history:
            axes.plot(history['t_s'], history['drag_n'], color=INDIGO, linewidth=1, label='drag')
            axes.plot(history['t_s'], history['lift_n'], color=ACCENT, linewidth=1, label='lift')
            if stats.get('window'):
                axes.axvspan(stats['window']['start_s'], stats['window']['end_s'], color=ACCENT, alpha=0.12, label='Averaging window')
            axes.legend(fontsize=7, frameon=False)
        axes.set_xlabel('Time [s]', fontsize=8); axes.set_ylabel('Force [N]', fontsize=8); axes.tick_params(labelsize=7); axes.grid(color=LINE, linewidth=0.5)
        spectrum = (stats.get('spectra') or {}).get(stats.get('cross_flow') or 'lift_n')
        axes = figure.add_axes([0.12, 0.08, 0.8, 0.2])
        if spectrum and len(spectrum['frequency_hz']) > 2:
            axes.semilogy(spectrum['frequency_hz'][1:], np.maximum(spectrum['psd'][1:], 1e-30), color=INDIGO, linewidth=1)
            if stats.get('dominant_frequency_hz'):
                axes.axvline(stats['dominant_frequency_hz'], color=ACCENT, linestyle='--', linewidth=1)
        else:
            axes.text(0.5, 0.5, 'No spectrum (too few samples)', ha='center', color=MUTED, transform=axes.transAxes)
        axes.set_xlabel('Frequency [Hz]', fontsize=8); axes.set_ylabel('Cross-flow force PSD', fontsize=8); axes.tick_params(labelsize=7); axes.grid(color=LINE, linewidth=0.5)

    def speed(self, figure):
        curve = (self.forces or {}).get('speed_curve')
        if not curve:
            self.paragraph(figure, 0.07, 0.9, 'Force and power curves need a freestream speed.')
            return
        v = [p['speed_m_s'] for p in curve['points']]
        for rect, key, label, scale in (([0.1, 0.55, 0.8, 0.33], 'drag_n', 'Drag force [N]', 1), ([0.1, 0.13, 0.8, 0.33], 'power_w', 'Drag power [kW]', 1e-3)):
            axes = figure.add_axes(rect)
            axes.plot(v, [p[key]*scale for p in curve['points']], color=INDIGO, linewidth=2)
            axes.axvline(self.spec.flow.speed, color=ACCENT, linestyle='--', linewidth=1, label='Simulated speed')
            axes.set_xlabel('Speed [m/s]', fontsize=8); axes.set_ylabel(label, fontsize=8); axes.tick_params(labelsize=8)
            axes.legend(fontsize=7, frameon=False); axes.grid(color=LINE, linewidth=0.5)
        self.paragraph(figure, 0.07, 0.07, curve['assumption'] + '.', size=7.5, color=MUTED)

    def drag(self, figure):
        self.paragraph(figure, 0.07, 0.915, 'Surface pressure coefficient (Cp): red regions push against the model, blue regions pull. '
                       'The red cloud marks where the flow has lost its total pressure (Cpt = 0), a visual measure of pressure drag.', size=8.5)
        positions = [[0.07, 0.5, 0.42, 0.33], [0.51, 0.5, 0.42, 0.33], [0.07, 0.12, 0.42, 0.33], [0.51, 0.12, 0.42, 0.33]]
        views = [('iso', 'cloud', '3D · loss cloud'), ('side', 'cloud', 'Side · loss cloud'), ('top', 'cloud', 'Top · loss cloud'), ('rear', 'cp', 'Rear · Cp')]
        for rect, (view, layer, caption) in zip(positions, views):
            self.image(figure, rect, f'renders/{view}_{layer}.png', caption)

    def findings(self, figure):
        y = 0.91
        items = (self.insights or {}).get('insights', [])
        if items:
            figure.text(0.07, y, 'Flow insights (rule-based observations)', fontsize=11, weight='bold', color=INK); y -= 0.025
            for item in items[:8]:
                figure.text(0.07, y, f"{item['severity'].upper()} · {item['title']}", fontsize=8.5, weight='bold', color=INDIGO); y -= 0.017
                y = self.paragraph(figure, 0.09, y, item['message'], width=100, size=8)
            y = self.paragraph(figure, 0.07, y, (self.insights or {}).get('disclaimer', ''), size=7, color=MUTED)
        figure.text(0.07, y-0.01, 'Numerical quality findings', fontsize=11, weight='bold', color=INK); y -= 0.035
        for finding in self.result.get('findings', [])[:18]:
            y = self.paragraph(figure, 0.07, y, f"{finding['status']} · {finding['code']}: {finding.get('detail', '')}", width=110, size=7.5)
            if y < 0.08:
                break

    def appendix(self, figure):
        from .exports import manifest
        text = json.dumps({'configuration': self.run['spec'], 'conventions': manifest(self.run)['conventions']}, indent=1)
        lines = text.splitlines()[:120]
        figure.text(0.07, 0.92, '\n'.join(lines), fontsize=5.2, family='monospace', va='top', color=INK)

    def build(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.PdfPages(target) as pdf:
            self.page(pdf, '', self.cover)
            self.page(pdf, 'Project details', self.details)
            self.page(pdf, '3D model', self.model)
            self.page(pdf, 'Performance', self.performance)
            self.page(pdf, 'Steady initialisation' if self.transient else 'Convergence', self.convergence_page)
            if self.transient and self.transient.get('plan'):
                self.page(pdf, 'Transient flow', self.transient_page)
            self.page(pdf, 'Force and power against speed', self.speed)
            self.page(pdf, 'Drag analysis', self.drag)
            self.page(pdf, 'Insights and quality', self.findings)
            self.page(pdf, 'Reproducibility appendix', self.appendix)
            info = pdf.infodict()
            info['Title'] = f'GustSim report · {self.spec.name}'
            info['Subject'] = ('Transient' if self.transient else 'Steady') + ' CFD results; experimental validation pending'
        return target


def build(run, spec, meta, case, target=None):
    return Report(run, spec, meta, case).build(target or Path(case)/'results/report.pdf')
