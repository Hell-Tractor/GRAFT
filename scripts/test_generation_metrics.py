import unittest
from generation_metrics import summarize_generation, summarize_admissions, distribution


class GenerationMetricsTests(unittest.TestCase):
    def test_pending_is_not_a_failed_response(self):
        overall=summarize_generation([],['a'])['overall']
        self.assertIsNone(overall['first_response_admission_rate'])
        self.assertIsNone(overall['eventual_admission_rate_of_finished_jobs'])
        self.assertEqual(overall['fixed_target_slots'],20)

    def test_preflight_rejection_keeps_denominator_without_inventing_a_model_dispatch(self):
        record={'crate':'a','finished':True,'accepted':False,'status':'planning_unknown',
                'core_preflight':{'status':'rejected','elapsed_seconds':1},
                'max_model_calls':2,'attempts':[]}
        overall=summarize_generation([record],['a'])['overall']
        self.assertEqual(overall['fixed_target_slots'],20)
        self.assertEqual(overall['generation_jobs_scheduled'],1)
        self.assertEqual(overall['generation_jobs_finished'],1)
        self.assertEqual(overall['llm_jobs_dispatched'],0)
        self.assertEqual(overall['total_model_calls'],0)
        self.assertEqual(overall['core_preflight_rejected_jobs'],1)
        self.assertEqual(overall['eventual_admission_rate_of_finished_jobs'],0)
        self.assertEqual(overall['max_model_calls_per_target_observed'],[2])

    def test_repair_success_keeps_initial_failure(self):
        record={'crate':'a','finished':True,'accepted':True,'elapsed_seconds':5,
                'attempts':[{'response_received':True,'accepted':False,'model_seconds':1},
                            {'response_received':True,'accepted':True,'model_seconds':2}]}
        overall=summarize_generation([record],['a'])['overall']
        self.assertEqual(overall['first_response_admission_rate'],0)
        self.assertEqual(overall['eventual_admission_rate_of_finished_jobs'],1)
        self.assertEqual(overall['admitted_fraction_of_fixed_slots_so_far'],.05)
        self.assertEqual(overall['model_call_time']['sum_seconds'],3)

    def test_backend_error_keeps_fixed_slots_and_availability_separate(self):
        record={'crate':'a','finished':True,'accepted':False,'backend_error':'TimeoutError',
                'attempts':[{'response_received':False,'accepted':False}]}
        overall=summarize_generation([record],['a'])['overall']
        self.assertIsNone(overall['first_response_admission_rate'])
        self.assertEqual(overall['eventual_admission_rate_of_finished_jobs'],0)
        self.assertEqual(overall['backend_error_jobs'],1)

    def test_native_batch_time_is_not_multiplied_by_targets(self):
        states={('RPG','a',i):{'status':'queued' if i<2 else 'no_target'} for i in range(20)}
        attempts={('RPG','a',i):[{'accepted':True,'elapsed_seconds':1}] for i in range(2)}
        row=summarize_admissions(['RPG'],['a'],states,attempts,{'RPG':{'a':{'generation':{'seconds':7}}}})['rows'][0]
        self.assertEqual(row['eventually_admitted'],2)
        self.assertEqual(row['eventual_admission_fraction_of_fixed_slots_so_far'],.1)
        self.assertEqual(row['native_generator_batch']['generation']['seconds'],7)
        self.assertEqual(row['admission_attempt_wall_time']['sum_seconds'],2)

    def test_timing_empty_and_percentile(self):
        self.assertIsNone(distribution([])['mean_seconds'])
        self.assertEqual(distribution([1,2,3,4])['p95_seconds'],4)

    def test_empty_and_truncated_responses_are_not_successes(self):
        record={'crate':'a','finished':True,'accepted':False,'status':'generation_failed',
                'backend_error':'EmptyModelResponse',
                'attempts':[{'response_received':False,'accepted':False,'empty_response':True,
                             'finish_reason':'length','content_length':0,
                             'usage':{'prompt_tokens':100,'completion_tokens':4096}}]}
        overall=summarize_generation([record],['a'])['overall']
        self.assertEqual(overall['first_responses_received'],0)
        self.assertEqual(overall['generation_failed_jobs'],1)
        self.assertEqual(overall['empty_response_attempts'],1)
        self.assertEqual(overall['length_truncated_attempts'],1)
        self.assertEqual(overall['completion_tokens'],4096)


if __name__=='__main__':unittest.main()
