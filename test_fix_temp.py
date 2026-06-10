import sys
sys.path.insert(0, 'D:\\Users\\WZW\\Pictures\\OnlineTest')

from problem_solver_agent.config import ML_KEYWORDS
from problem_solver_agent.pipeline import reclassify_problem_type, map_final_type, determine_solver

print('Config imports: OK')
print('Pipeline imports: OK')
result = reclassify_problem_type('GENERAL', 'numpy')
print(f'Test function result: {result}')
assert result == 'ML_CODING', f'Expected ML_CODING, got {result}'
print('All tests passed!')