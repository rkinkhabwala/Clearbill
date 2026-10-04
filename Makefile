.PHONY: setup models doctor test synth eval-rules eval ui

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[ui,dev]"

models:
	ollama pull gemma4
	ollama pull qwen3:8b

doctor:
	clearbill doctor

test:
	pytest -q

synth:
	python eval/make_synthetic.py --n 24 --out eval/cases

eval-rules: synth
	python eval/run_eval.py --cases eval/cases --rules-only

eval: synth
	python eval/run_eval.py --cases eval/cases

ui:
	streamlit run src/clearbill/app.py
