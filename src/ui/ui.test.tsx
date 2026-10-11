import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as RadixTooltip from '@radix-ui/react-tooltip';
import { EvidenceStrip, QuantityField, Segmented } from './index';
import { usePreferences } from '../state/preferences';
import { useState } from 'react';

describe('ui primitives', () => {
  it('shows all four evidence states with pending validation', () => {
    render(<RadixTooltip.Provider><EvidenceStrip block={{ execution: 'completed', numerical: 'review_required', validation: 'experimental_validation_pending', geometry: 'imported' }} /></RadixTooltip.Provider>);
    expect(screen.getByText('Experimental validation')).toBeInTheDocument();
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('Review required')).toBeInTheDocument();
    expect(screen.queryByText(/^Validated$/)).toBeNull();
  });

  it('edits SI values in display units', async () => {
    usePreferences.setState({ units: { ...usePreferences.getState().units, speed: 'km/h' } });
    function Harness() { const [v, setV] = useState(10); return <><QuantityField label="Speed" quantity="speed" value={v} onChange={setV} /><output>{v}</output></>; }
    render(<Harness />);
    const input = screen.getByLabelText('Speed') as HTMLInputElement;
    expect(Number(input.value)).toBeCloseTo(36);
    await userEvent.clear(input);
    await userEvent.type(input, '72');
    expect(Number(screen.getByRole('status', { hidden: true })?.textContent ?? screen.getByText(/^20/).textContent)).toBeCloseTo(20);
  });

  it('segmented control is a radio group', async () => {
    function Harness() { const [v, setV] = useState<'a' | 'b'>('a'); return <Segmented label="Choice" value={v} onChange={setV} options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]} />; }
    render(<Harness />);
    await userEvent.click(screen.getByRole('radio', { name: 'B' }));
    expect(screen.getByRole('radio', { name: 'B' })).toHaveAttribute('aria-checked', 'true');
  });
});
