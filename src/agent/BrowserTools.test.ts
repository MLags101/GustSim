import { describe, expect, it } from 'vitest';
import { browserTools } from './BrowserTools';

describe('browser agent tools', () => {
  it('keeps the published tool names (parity with docs/AGENT.md and gustsim mcp)', () => {
    expect(browserTools().map((t) => t.name)).toEqual([
      'read_gustsim_workspace', 'validate_gustsim_setup', 'configure_gustsim_preset', 'confirm_gustsim_setup',
      'queue_gustsim_mesh', 'review_gustsim_mesh', 'queue_gustsim_solve', 'read_gustsim_run',
    ]);
  });
  it('refuses to act without an open setup', async () => {
    const validate = browserTools().find((t) => t.name === 'validate_gustsim_setup')!;
    await expect(validate.execute()).rejects.toThrow(/Open a setup/);
  });
  it('reports experimental validation as pending when nothing has run', async () => {
    const read = browserTools().find((t) => t.name === 'read_gustsim_workspace')!;
    const out: any = await read.execute();
    expect(out.run.experimental_validation).toBe('experimental_validation_pending');
    expect(out.mesh.experimental_validation).toBe('experimental_validation_pending');
  });
});
