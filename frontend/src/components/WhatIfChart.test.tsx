import { changeText, signedTicks } from "./WhatIfChart";

it("builds clean axis ticks across zero", () => {
  expect(signedTicks(-591.8e6, 140.6e6)).toEqual([-600e6, -400e6, -200e6, 0, 200e6]);
  expect(signedTicks(0, 95e6)).toEqual([0, 20e6, 40e6, 60e6, 80e6, 100e6]);
  expect(signedTicks(0, 0)[0]).toBe(0);
});

it("describes the change in words", () => {
  expect(changeText(-120e6, -107e6)).toBe("Shortfall shrinks by 13.0M");
  expect(changeText(-20e6, 5e6)).toBe("Shortfall turns into a surplus");
  expect(changeText(10e6, 30e6)).toBe("Surplus grows by 20.0M");
  expect(changeText(30e6, 10e6)).toBe("Surplus shrinks by 20.0M");
  expect(changeText(19.8e6, -94.8e6)).toBe("Surplus turns into a shortfall");
  expect(changeText(-5e6, -50e6)).toBe("Shortfall deepens by 45.0M");
  expect(changeText(1e6, 1e6)).toBe("No change");
});
