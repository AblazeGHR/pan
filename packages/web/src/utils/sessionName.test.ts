import { describe, expect, it } from 'vitest';
import { nextSessionDefaultName } from './sessionName';

describe('nextSessionDefaultName', () => {
  it('无任何 session 时从 1 开始', () => {
    expect(nextSessionDefaultName([])).toBe('session-1');
  });

  it('无 session-N 形式命名（全是自定义名）时从 1 开始', () => {
    expect(
      nextSessionDefaultName([{ name: 'code-review' }, { name: 'debug' }]),
    ).toBe('session-1');
  });

  it('序号连续时取下一个', () => {
    expect(
      nextSessionDefaultName([
        { name: 'session-1' },
        { name: 'session-2' },
        { name: 'session-3' },
      ]),
    ).toBe('session-4');
  });

  it('中间缺失时复用最小可用序号（核心：删除后回收）', () => {
    expect(
      nextSessionDefaultName([
        { name: 'session-2' },
        { name: 'session-5' },
      ]),
    ).toBe('session-1');
  });

  it('删掉 session-1~4 只剩 session-5 时回到 session-1（用户报告的 bug 场景）', () => {
    expect(
      nextSessionDefaultName([{ name: 'session-5' }]),
    ).toBe('session-1');
  });

  it('自定义名与 session-N 混排时只按序号回收', () => {
    expect(
      nextSessionDefaultName([
        { name: 'session-1' },
        { name: 'random-task' },
        { name: 'session-2' },
      ]),
    ).toBe('session-3');
  });

  it('序号乱序/大跳时不误判（session-100 存在时仍从 1 起找）', () => {
    expect(
      nextSessionDefaultName([{ name: 'session-100' }]),
    ).toBe('session-1');
  });

  it('前后空白与大小写敏感处理（session-1 带空格可识别）', () => {
    expect(
      nextSessionDefaultName([{ name: '  session-1  ' }, { name: 'Session-2' }]),
    ).toBe('session-2');
  });
});
