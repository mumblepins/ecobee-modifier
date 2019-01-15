import matplotlib.pyplot as plt
import numpy as np

fan_factors = [0.43651, -5.99206, 29.9206, -19.3651]
# (sensor delta, fan runtime)
data_points = np.array([
    [1, 5],
    [2, 15],
    [5,25],
    [6, 30],
    [7,40],
    [8, 55]
])
polynomial_order = 8

polynomial_order = min(min(polynomial_order, len(data_points[:, 0])-1),5)
fan_factors = np.polyfit(
    data_points[:, 0],
    data_points[:, 1],
    polynomial_order
)
fan_factors = list(fan_factors)
print('"FAN_FACTORS={}"'.format(fan_factors))
fan_factors = [0] * (6 - len(fan_factors)) + fan_factors
print('"FAN_FACTORS={}"'.format(fan_factors))
x = np.linspace(0, 15, 50)
runtime = fan_factors[0] * np.power(x, 5) + \
          fan_factors[1] * np.power(x, 4) + \
          fan_factors[2] * np.power(x, 3) + \
          fan_factors[3] * np.power(x, 2) + \
          fan_factors[4] * x + \
          fan_factors[5]
plt.plot(data_points[:, 0], data_points[:, 1], 'bo')
plt.plot(x, runtime)
plt.ylim(0, 60)
xmax = x[-1]
for k, v in enumerate(runtime):
    if v > 60:
        xmax = x[k]
        break
plt.xlim(0, xmax)
plt.show()
