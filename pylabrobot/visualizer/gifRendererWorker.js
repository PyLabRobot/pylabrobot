importScripts('./gif.js');

let gif;

onmessage = function (e) {
  const frames = e.data.frames;
  const options = e.data.options;

  gif = new GIF(options);
  frames.forEach(frame => {
    gif.addFrame(frame.canvas, { copy: true, delay: options.delay });
  });

  gif.on('finished', function (blob) {
    postMessage({ url: URL.createObjectURL(blob) });
  });

  gif.render();
};