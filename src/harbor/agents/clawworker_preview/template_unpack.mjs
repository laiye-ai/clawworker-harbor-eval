// Bundled against the pinned Host sources by build.py, not a second ZIP validator.
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { unpackTemplate } from 'clawworker-template-package';
import { templateName, templateVersion } from 'clawworker-template-definition';

const temporary = mkdtempSync(join(tmpdir(), 'harbor-template-'));
try {
  const request = JSON.parse(process.argv[2]);
  if (typeof request.templateId !== 'string' || !templateName.test(request.templateId)
      || typeof request.templateVersion !== 'string' || !templateVersion.test(request.templateVersion)) {
    throw new Error('template_package_invalid');
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 50 * 1024 ** 2) throw new Error('template_package_invalid');
    chunks.push(chunk);
  }
  unpackTemplate(Buffer.concat(chunks), request, join(temporary, 'definition'));
  console.log(JSON.stringify({ validated: true }));
} catch {
  console.error('template_package_invalid');
  process.exitCode = 1;
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
