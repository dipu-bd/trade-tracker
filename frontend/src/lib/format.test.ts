import { describe, expect, it } from 'vitest'

import { ago, money, num, percent, qty, signed, tone } from './format'

describe('money', () => {
  it('renders a decimal string from the API', () => {
    expect(money('1234.5')).toBe('$1,234.50')
  })

  it('shows a dash rather than NaN for missing values', () => {
    expect(money(null)).toBe('—')
    expect(money(undefined)).toBe('—')
    expect(money('not-a-number')).toBe('—')
  })
})

describe('percent', () => {
  it('scales a fraction to a percentage', () => {
    expect(percent(0.1234)).toBe('12.34%')
    expect(percent(-0.05, 1)).toBe('-5.0%')
  })

  it('does not invent a value when none is given', () => {
    expect(percent(null)).toBe('—')
  })
})

describe('qty', () => {
  it('keeps crypto precision rather than rounding a holding away', () => {
    expect(qty('0.07928158')).toBe('0.07928158')
  })
})

describe('num', () => {
  it('coerces unknown API values without throwing', () => {
    expect(num('12.5')).toBe(12.5)
    expect(num(undefined)).toBe(0)
    expect(num({})).toBe(0)
    expect(num('abc')).toBe(0)
  })
})

describe('signed', () => {
  it('marks direction explicitly', () => {
    expect(signed(1.5)).toBe('+1.50')
    expect(signed(-1.5)).toBe('-1.50')
  })
})

describe('tone', () => {
  it('colours gains and losses differently and neutral not at all', () => {
    expect(tone(1)).toContain('gain')
    expect(tone(-1)).toContain('loss')
    expect(tone(0)).toBe('')
  })
})

describe('ago', () => {
  it('reports staleness in the largest sensible unit', () => {
    const now = Date.now()
    expect(ago(new Date(now - 30_000).toISOString())).toMatch(/s ago$/)
    expect(ago(new Date(now - 600_000).toISOString())).toMatch(/m ago$/)
    expect(ago(new Date(now - 7_200_000).toISOString())).toMatch(/h ago$/)
  })

  it('handles a never-quoted instrument', () => {
    expect(ago(null)).toBe('—')
  })
})
