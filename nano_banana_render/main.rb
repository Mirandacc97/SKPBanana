require 'sketchup.rb'
require 'json'
require 'tmpdir'
require 'time'
require 'securerandom'
require 'fileutils'

module NanoBananaRender
  PLUGIN_DIR = File.join(File.dirname(File.dirname(__FILE__)), 'nano_banana_render')
  CONFIG_FILE = File.join(PLUGIN_DIR, 'config.json')

  DEFAULT_CONFIG = {
    'api_key' => '',
    'model' => 'gemini-2.5-flash-image',
    'num_options' => 1,
    'last_prompt' => ''
  }.freeze

  @config = nil
  @dialog = nil
  @results_dialog = nil

  class << self
    def config
      @config ||= load_config
    end

    def load_config
      if File.exist?(CONFIG_FILE)
        DEFAULT_CONFIG.merge(JSON.parse(File.read(CONFIG_FILE)))
      else
        DEFAULT_CONFIG.dup
      end
    rescue StandardError
      DEFAULT_CONFIG.dup
    end

    def save_config
      FileUtils.mkdir_p(File.dirname(CONFIG_FILE))
      File.write(CONFIG_FILE, JSON.pretty_generate(config.reject { |k, _| k == 'api_key' }))
    end

    def render_settings
      settings = { 'provider' => 'codex', 'codex_executable' => 'codex',
                   'agent_model' => '', 'image_model' => 'chatgpt-image-latest' }
      File.foreach(File.join(PLUGIN_DIR, 'render.conf'), encoding: 'bom|utf-8') do |line|
        next if line.strip.empty? || line.lstrip.start_with?('#')
        key, value = line.split('=', 2).map(&:strip)
        settings[key] = value if settings.key?(key) && value
      end
      settings
    end

    def capture_view(filepath)
      view = Sketchup.active_model.active_view
      keys = {
        filename: filepath,
        width: view.vpwidth * 2,
        height: view.vpheight * 2,
        antialias: true,
        transparent: false
      }
      raise 'Viewport capture failed' unless view.write_image(keys)
      filepath
    end

    def guarded_callback(dialog, name)
      dialog.add_action_callback(name) do |*args|
        begin
          yield(*args)
        rescue StandardError => e
          report_failure(dialog, run_directory, "#{name}: #{e.class}: #{e.message}")
        end
      end
    end

    def run_directory
      root = File.join(ENV['LOCALAPPDATA'] || Dir.tmpdir, 'SKPBanana', 'runs')
      path = File.join(root, "#{Time.now.strftime('%Y%m%d-%H%M%S')}-#{SecureRandom.hex(4)}")
      FileUtils.mkdir_p(path)
      path
    end

    def diagnose(dir, message)
      line = "#{Time.now.iso8601} #{message}"
      puts "[SKPBanana] #{line}"
      File.open(File.join(dir, 'diagnostic.log'), 'a:utf-8') { |f| f.puts(line) }
    end

    def report_failure(dialog, dir, message)
      diagnose(dir, message)
      text = "#{message} — #{dir}"
      if dialog.visible?
        dialog.execute_script("setLoading(false); showStatus(#{JSON.generate(text)}, 'error')")
      else
        UI.messagebox(text)
      end
    end

    def run_worker(dialog, job, dir: run_directory, &on_success)
      python = ENV.fetch('SKPBANANA_PYTHON', 'python')
      stdout = File.join(dir, 'response.json')
      stderr = File.join(dir, 'worker.log')
      input, writer = IO.pipe
      pid = Process.spawn(python, File.join(PLUGIN_DIR, 'worker.py'), in: input,
                          out: stdout, err: stderr)
      input.close
      writer.binmode
      writer.write(JSON.generate(job))
      writer.close
      diagnose(dir, "started pid=#{pid} provider=#{job[:provider]} action=#{job[:action]}")
      started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      limit = 630 * (job[:count] || 1)
      timer = UI.start_timer(0.25, true) do
        begin
          ended = Process.waitpid2(pid, Process::WNOHANG)
          unless ended
            if Process.clock_gettime(Process::CLOCK_MONOTONIC) - started > limit
              Process.kill('KILL', pid)
              Process.waitpid(pid)
              raise "Worker timed out after #{limit}s"
            end
            next
          end
          UI.stop_timer(timer)
          status = ended.last
          diagnose(dir, "exit=#{status.exitstatus} response_bytes=#{File.size(stdout)} stderr_bytes=#{File.size(stderr)}")
          raise "Python exited with status #{status.exitstatus}. See worker.log" unless status.success?
          begin
            reply = JSON.parse(File.read(stdout, encoding: 'utf-8'))
          rescue JSON::ParserError
            raise 'Invalid worker JSON. See worker.log and response.json'
          end
          raise reply['error'] if reply['error']
          dialog.execute_script("setLoading(false)") if dialog.visible?
          on_success.call(reply.fetch('value'), dir)
        rescue StandardError => e
          UI.stop_timer(timer)
          report_failure(dialog, dir, "#{e.class}: #{e.message}")
        end
      end
    rescue StandardError => e
      Process.detach(pid) if pid
      report_failure(dialog, dir, "#{e.class}: #{e.message}")
    ensure
      input.close if input && !input.closed?
      writer.close if writer && !writer.closed?
    end

    def show_prompt_dialog
      if @dialog && @dialog.visible?
        @dialog.bring_to_front
        return
      end

      html_path = File.join(PLUGIN_DIR, 'html', 'prompt_dialog.html')

      @dialog = UI::HtmlDialog.new({
        dialog_title: 'Nano Banana Pro Render',
        width: 520,
        height: 620,
        resizable: true,
        style: UI::HtmlDialog::STYLE_DIALOG
      })

      guarded_callback(@dialog, 'get_config') do |_ctx|
        @dialog.execute_script("setConfig(#{JSON.generate(config.merge(render_settings))})")
      end

      guarded_callback(@dialog, 'save_api_key') do |_ctx, key|
        config['api_key'] = key.to_s.strip
        save_config
        @dialog.execute_script("showStatus('API key set for this session', 'success')")
      end

      guarded_callback(@dialog, 'enhance_prompt') do |_ctx, prompt, provider|
        if provider == 'gemini' && config['api_key'].empty?
          @dialog.execute_script("showStatus('Set your API key first', 'error')")
          next
        end

        dialog = @dialog
        job = { action: 'enhance', provider: provider, settings: render_settings, key: config['api_key'], prompt: prompt }
        dialog.execute_script("setLoading(true)")
        run_worker(dialog, job) do |enhanced|
          dialog.execute_script("setEnhancedPrompt(#{JSON.generate(enhanced)})")
        end
      end

      guarded_callback(@dialog, 'render') do |_ctx, prompt, num_options, model_name, provider|
        if provider == 'gemini' && config['api_key'].empty?
          @dialog.execute_script("showStatus('Set your API key first', 'error')")
          next
        end

        num = num_options.to_i
        num = 1 if num < 1
        num = 4 if num > 4
        config['num_options'] = num
        config['last_prompt'] = prompt
        config['model'] = model_name.to_s if model_name && !model_name.to_s.empty?
        save_config

        @dialog.execute_script("setLoading(true)")

        begin
          dir = run_directory
          capture = File.join(dir, 'original.png')
          capture_view(capture)
          diagnose(dir, "captured bytes=#{File.size(capture)}")
          job = { action: 'render', provider: provider, settings: render_settings, key: config['api_key'], model: config['model'],
                  result_dir: dir, capture: capture, prompt: prompt, count: num }
          run_worker(@dialog, job, dir: dir) do |result|
            show_results_dialog(result, dir)
            failures = result.fetch('renders').reject { |item| item['success'] }
            if failures.any?
              report_failure(@dialog, dir, failures.first.fetch('error'))
            elsif @dialog.visible?
              @dialog.execute_script("showStatus(#{JSON.generate("Results: #{dir}")}, 'success')")
            end
          end
        rescue StandardError => e
          diagnose(dir, e.message) if dir
          @dialog.execute_script("setLoading(false)")
          @dialog.execute_script("showStatus(#{JSON.generate(e.message)}, 'error')")
        end
      end

      @dialog.set_file(html_path)
      @dialog.show
    end

    def show_results_dialog(result, dir)
      if @results_dialog && @results_dialog.visible?
        @results_dialog.close
      end

      html_path = File.join(PLUGIN_DIR, 'html', 'results_dialog.html')

      @results_dialog = UI::HtmlDialog.new({
        dialog_title: 'Render Results - Nano Banana Pro',
        width: 1000,
        height: 750,
        resizable: true,
        style: UI::HtmlDialog::STYLE_DIALOG
      })

      dialog = @results_dialog
      dialog.add_action_callback('preview_status') do |_ctx, status|
        diagnose(dir, "preview #{status.to_s[0, 80]}")
      end
      guarded_callback(dialog, 'get_results') do |_ctx|
        dialog.execute_script("loadResults(#{JSON.generate(result)})")
      end

      guarded_callback(@results_dialog, 'save_image') do |_ctx, index|
        render = result.fetch('renders').fetch(Integer(index))
        next unless render['success']
        mime = render['mime']
        ext = { 'image/jpeg' => '.jpg', 'image/webp' => '.webp' }.fetch(mime, '.png')
        path = UI.savepanel('Save Rendered Image', '', "render#{ext}")
        if path
          path += ext unless path.downcase.end_with?(ext)
          FileUtils.cp(File.join(dir, File.basename(render.fetch('file'))), path)
          UI.messagebox("Image saved to:\n#{path}")
        end
      end

      preview = File.join(dir, 'preview.html')
      FileUtils.cp(html_path, preview)
      diagnose(dir, "opening preview variations=#{result.fetch('renders').size}")
      @results_dialog.set_file(preview)
      @results_dialog.show
    end

    def setup_menu
      menu = UI.menu('Plugins')
      submenu = menu.add_submenu('Nano Banana Pro Render')

      submenu.add_item('Render Current View') { show_prompt_dialog }
      submenu.add_item('Open diagnostic folder') do
        root = File.join(ENV['LOCALAPPDATA'] || Dir.tmpdir, 'SKPBanana', 'runs')
        FileUtils.mkdir_p(root)
        UI.openURL("file:///" + root.tr('\\', '/').sub(%r{^/}, '').gsub(' ', '%20'))
      end
      submenu.add_item('Settings') { show_prompt_dialog }

      toolbar = UI::Toolbar.new('Nano Banana Pro')
      cmd = UI::Command.new('Render') { show_prompt_dialog }
      cmd.tooltip = 'Render with Nano Banana Pro'
      cmd.status_bar_text = 'Capture view and render with AI'

      icon_path = File.join(PLUGIN_DIR, 'icons', 'render')
      cmd.small_icon = "#{icon_path}_24.png" if File.exist?("#{icon_path}_24.png")
      cmd.large_icon = "#{icon_path}_32.png" if File.exist?("#{icon_path}_32.png")

      toolbar.add_item(cmd)
      toolbar.show
    end
  end

  setup_menu

end
